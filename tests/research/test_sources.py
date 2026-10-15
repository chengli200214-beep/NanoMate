"""Source adapters use pinned mock transports, never a live site or model."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from nanomuse.research.clock import FakeClock
from nanomuse.research.sources import FixtureFetcher, HttpSourceFetcher, SourceError

PUBLIC_IP = "93.184.216.34"
REPO = "https://github.com/research-example/sample"
SHA = "a" * 40


async def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def fetcher(handler: Any, **kwargs: Any) -> HttpSourceFetcher:
    return HttpSourceFetcher(
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
        clock=FakeClock(),
        **kwargs,
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://files.example/readme.txt",
        "http://localhost/",
        "http://LOCALHOST./",
        "http://host.internal/",
        "http://host.local/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://169.254.169.254/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://2130706433/",
        "http://0177.0.0.1/",
        "http://0x7f000001/",
        "https://user:password@docs.example/",
        "https://docs.example\\@localhost/",
        "https://docs.example/\nlocalhost",
        "https://docs.example:0/",
    ],
)
async def test_unsafe_url_is_rejected_before_transport(url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("an unsafe URL reached the HTTP transport")

    with pytest.raises(SourceError) as error:
        await fetcher(handler).fetch({"kind": "document", "url": url})
    assert error.value.code == "unsafe_url"
    assert error.value.retryable is False


async def test_mixed_public_and_private_dns_answer_is_rejected() -> None:
    calls = []

    async def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC_IP, "127.0.0.1"]

    client = HttpSourceFetcher(
        resolver=resolver,
        transport=httpx.MockTransport(lambda request: calls.append(request)),
    )
    with pytest.raises(SourceError, match="public Internet"):
        await client.fetch({"kind": "document", "url": "https://docs.example/"})
    assert calls == []


async def test_connection_pins_validated_ip_and_rechecks_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions = []
    requests = []

    async def rebinding_resolver(host: str, port: int) -> list[str]:
        resolutions.append((host, port))
        return [PUBLIC_IP] if len(resolutions) == 1 else ["127.0.0.1"]

    def no_system_dns(*args: Any, **kwargs: Any) -> None:
        pytest.fail("the transport tried to resolve the checked hostname again")

    monkeypatch.setattr(socket, "getaddrinfo", no_system_dns)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == PUBLIC_IP
        assert request.headers["host"] == "docs.example"
        assert request.extensions["sni_hostname"] == "docs.example"
        return httpx.Response(200, text="# Public documentation")

    client = HttpSourceFetcher(resolver=rebinding_resolver, transport=httpx.MockTransport(handler))
    result = await client.fetch({"kind": "document", "url": "https://docs.example/readme"})
    assert result.url == "https://docs.example/readme"
    with pytest.raises(SourceError):
        await client.fetch({"kind": "document", "url": "https://docs.example/readme"})
    assert len(requests) == 1
    assert len(resolutions) == 2


async def test_each_redirect_is_checked_and_auth_is_not_forwarded() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["host"] == "api.github.com":
            assert request.headers["authorization"] == "Bearer synthetic-test-value"
            return httpx.Response(302, headers={"location": "https://redirect.example/metadata"})
        assert "authorization" not in request.headers
        assert "if-none-match" not in request.headers
        return httpx.Response(200, json={"full_name": "research-example/sample"})

    result = await fetcher(handler, github_token="synthetic-test-value").fetch(
        {"kind": "metadata", "url": REPO}, {"etag": '"old"', "content": "old"}
    )
    assert "research-example/sample" in result.content
    assert len(requests) == 2


async def test_redirect_to_private_address_never_makes_a_second_request() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    with pytest.raises(SourceError) as error:
        await fetcher(handler).fetch({"kind": "document", "url": "https://docs.example/"})
    assert error.value.code == "unsafe_url"
    assert len(requests) == 1


async def test_relative_redirect_rechecks_dns() -> None:
    resolutions = 0
    requests = []

    async def resolver(host: str, port: int) -> list[str]:
        nonlocal resolutions
        resolutions += 1
        return [PUBLIC_IP] if resolutions == 1 else ["10.0.0.1"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "/moved"})

    client = HttpSourceFetcher(resolver=resolver, transport=httpx.MockTransport(handler))
    with pytest.raises(SourceError):
        await client.fetch({"kind": "document", "url": "https://docs.example/"})
    assert resolutions == 2
    assert len(requests) == 1


async def test_proxy_environment_does_not_change_the_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:9")
    monkeypatch.setenv("HTTP_PROXY", "http://localhost:9")
    monkeypatch.setenv("ALL_PROXY", "socks5://localhost:9")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == PUBLIC_IP
        return httpx.Response(200, text="public")

    result = await fetcher(handler).fetch({"kind": "document", "url": "https://docs.example/"})
    assert result.content == "public"


async def test_readme_is_fetched_at_the_resolved_commit_and_etag_tracks_ref() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["host"] == "api.github.com"
        if request.url.path.endswith("/commits/HEAD"):
            return httpx.Response(200, json={"sha": SHA}, headers={"etag": '"commit-ref"'})
        assert request.url.params["ref"] == SHA
        return httpx.Response(
            200,
            json={
                "path": "README.md",
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(b"# Sample\nRuns on Windows.").decode(),
            },
            headers={"etag": '"file-etag"'},
        )

    result = await fetcher(handler).fetch({"kind": "readme", "url": REPO})
    assert paths == [
        "/repos/research-example/sample/commits/HEAD",
        "/repos/research-example/sample/readme",
    ]
    assert result.version == SHA
    assert result.url == f"{REPO}/blob/{SHA}/README.md"
    assert result.content == "# Sample\nRuns on Windows."
    assert result.etag == '"commit-ref"'


@pytest.mark.parametrize("host_prefix", ["https://github.com", "https://raw.githubusercontent.com"])
async def test_explicit_file_url_is_pinned(host_prefix: str) -> None:
    blob = "/blob" if "github.com" in host_prefix else ""
    url = host_prefix + f"/research-example/sample{blob}/main/src/app.py"

    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            assert request.url.path.endswith("/main")
            return httpx.Response(200, json={"sha": SHA})
        assert request.url.path.endswith("/contents/src/app.py")
        assert request.url.params["ref"] == SHA
        return httpx.Response(200, json={"encoding": "base64", "content": "cHJpbnQoMSk="})

    result = await fetcher(handler).fetch({"kind": "file", "url": url})
    assert result.url == f"{REPO}/blob/{SHA}/src/app.py"
    assert result.content == "print(1)"


async def test_file_kind_requires_an_explicit_file_url() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(lambda request: pytest.fail("no network expected")).fetch(
            {"kind": "file", "url": REPO}
        )
    assert error.value.code == "invalid_source"


async def test_github_304_reuses_saved_pinned_snapshot() -> None:
    previous = {
        "content": "README from the last successful check",
        "url": f"{REPO}/blob/{SHA}/README.md",
        "version": SHA,
        "etag": '"commit-ref"',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == previous["etag"]
        assert request.url.path.endswith("/commits/HEAD")
        return httpx.Response(304)

    result = await fetcher(handler).fetch({"kind": "readme", "url": REPO}, previous)
    assert result.not_modified is True
    assert result.content == previous["content"]
    assert result.url == previous["url"]
    assert result.version == SHA


async def test_document_cleanup_keeps_main_version_and_excludes_page_chrome() -> None:
    html = """<html><head><title>Deployment</title><script>ignore()</script></head>
        <body><nav>Products</nav><header>2026-10-15</header><main><h1>Deployment</h1>
        <p>Version 2 requires Python 3.11.</p><p hidden>hidden text</p>
        <aside>Try another product</aside><script>bad()</script></main>
        <footer>Site modified yesterday</footer></body></html>"""
    result = await fetcher(
        lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    ).fetch({"kind": "document", "url": "https://docs.example/deploy"})
    assert "Version 2 requires Python 3.11." in result.content
    assert result.title == "Deployment"
    assert all(
        noise not in result.content
        for noise in ("Products", "2026-10-15", "hidden text", "Try another", "bad()", "yesterday")
    )
    assert result.version.startswith("sha256:")


async def test_document_etag_304_reuses_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"v2"'
        return httpx.Response(304, headers={"etag": '"v2"'})

    previous = {"content": "Version 2", "etag": '"v2"', "version": "hash-v2"}
    result = await fetcher(handler).fetch(
        {"kind": "document", "url": "https://docs.example/"}, previous
    )
    assert result.not_modified
    assert result.content == "Version 2"
    assert result.version == "hash-v2"


async def test_304_without_snapshot_is_an_error() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(lambda request: httpx.Response(304)).fetch(
            {"kind": "document", "url": "https://docs.example/"}
        )
    assert error.value.code == "invalid_response"


async def test_manual_text_does_not_fetch_and_detects_unchanged_content() -> None:
    client = fetcher(lambda request: pytest.fail("manual text made a request"))
    source = {"kind": "manual", "content": " # Requirements\nWindows only. ", "title": "Notes"}
    result = await client.fetch(source)
    repeated = await client.fetch(source, result.model_dump())
    assert result.content == "# Requirements\nWindows only."
    assert result.title == "Notes"
    assert result.version.startswith("sha256:")
    assert repeated.not_modified
    assert result.fetched_at == "2027-01-15T08:00:00+00:00"


@pytest.mark.parametrize("kind", ["metadata", "releases"])
async def test_github_metadata_and_releases_are_read_only_json(kind: str) -> None:
    payload: Any = {
        "full_name": "research-example/sample",
        "license": {"spdx_id": "GPL-3.0", "name": "GPL", "url": "ignored"},
    }
    if kind == "releases":
        payload = [{"id": 7, "tag_name": "v2", "body": "Dependency changed.", "assets": []}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if kind == "releases":
            assert request.url.params["per_page"] == "20"
        return httpx.Response(200, json=payload, headers={"etag": '"data"'})

    result = await fetcher(handler).fetch({"kind": kind, "url": REPO})
    data = json.loads(result.content)
    assert result.etag == '"data"'
    assert result.version.startswith("sha256:")
    assert "assets" not in result.content
    if kind == "metadata":
        assert "url" not in data["license"]


async def test_rate_limit_retry_after_is_exposed_without_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="must not enter the error", headers={"retry-after": "12"})

    with pytest.raises(SourceError) as error:
        await fetcher(handler).fetch({"kind": "readme", "url": REPO})
    assert error.value.code == "rate_limited"
    assert error.value.retryable
    assert error.value.retry_after == 12
    assert "must not enter" not in str(error.value)


async def test_authentication_failure_is_not_retried() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(lambda request: httpx.Response(401)).fetch({"kind": "metadata", "url": REPO})
    assert error.value.code == "authentication"
    assert not error.value.retryable


class _Chunks(httpx.AsyncByteStream):
    async def __aiter__(self) -> Any:
        yield b"a" * 40000
        yield b"b" * 40000


async def test_streamed_body_limit_is_enforced_without_content_length() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(
            lambda request: httpx.Response(200, stream=_Chunks()), max_body_bytes=65536
        ).fetch({"kind": "document", "url": "https://docs.example/"})
    assert error.value.code == "size_limit"


async def test_declared_body_limit_is_enforced() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(
            lambda request: httpx.Response(200, headers={"content-length": "999"}),
            max_body_bytes=100,
        ).fetch({"kind": "document", "url": "https://docs.example/"})
    assert error.value.code == "size_limit"


async def test_fixture_defaults_are_offline_real_attributed_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        pytest.fail("fixture replay touched the network")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    client = FixtureFetcher()
    assert client.fixture is True
    for url in (
        "https://github.com/nano-muse/nanoMuse",
        "https://github.com/Anil-matcha/open-dots",
    ):
        result = await client.fetch({"kind": "readme", "url": url})
        assert len(result.version) == 40
        assert result.version in result.url
        assert len(result.content) > 100


async def test_fixture_file_requires_explicit_mode_and_exact_key(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": {}}), encoding="utf-8")
    with pytest.raises(SourceError) as error:
        FixtureFetcher(path)
    assert error.value.code == "invalid_fixture"
    path.write_text(json.dumps({"fixture": True, "sources": {}}), encoding="utf-8")
    with pytest.raises(SourceError) as missing:
        await FixtureFetcher(path).fetch({"kind": "document", "url": "https://docs.example/"})
    assert missing.value.code == "fixture_missing"


async def test_fixture_does_not_allow_local_source_or_local_saved_url() -> None:
    for source_url, saved_url in (
        ("http://127.0.0.1/readme", "https://docs.example/"),
        ("https://docs.example/", "http://127.0.0.1/readme"),
    ):
        client = FixtureFetcher({"source": {"url": saved_url, "content": "text"}})
        with pytest.raises(SourceError) as error:
            await client.fetch({"id": "source", "kind": "document", "url": source_url})
        assert error.value.code == "unsafe_url"


async def test_fixture_source_kind_distinguishes_readme_from_releases() -> None:
    fixture = {f"readme:{REPO}": {"url": REPO, "content": "README", "version": "fixed"}}
    client = FixtureFetcher(fixture)
    readme = await client.fetch({"kind": "readme", "url": REPO})
    assert readme.content == "README"
    with pytest.raises(SourceError) as error:
        await client.fetch({"kind": "releases", "url": REPO})
    assert error.value.code == "fixture_missing"


async def test_encoded_branch_slash_is_preserved_when_resolving_file() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            assert b"feature%2Fsub" in request.url.raw_path
            return httpx.Response(200, json={"sha": SHA})
        assert request.url.path.endswith("/contents/src/app.py")
        return httpx.Response(200, json={"encoding": "base64", "content": "cHJpbnQoMSk="})

    result = await fetcher(handler).fetch(
        {"kind": "file", "url": f"{REPO}/blob/feature%2Fsub/src/app.py"}
    )
    assert result.version == SHA


async def test_request_deadline_includes_dns() -> None:
    async def stalled(host: str, port: int) -> list[str]:
        await asyncio.Event().wait()
        return [PUBLIC_IP]

    client = HttpSourceFetcher(
        resolver=stalled,
        transport=httpx.MockTransport(lambda request: pytest.fail("DNS did not finish")),
        timeout=0.01,
    )
    with pytest.raises(SourceError) as error:
        await client.fetch({"kind": "document", "url": "https://docs.example/"})
    assert error.value.code == "timeout"
    assert error.value.retryable


async def test_github_rate_limit_reset_is_relative_to_injected_clock() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(
            lambda request: httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000060"},
            )
        ).fetch({"kind": "metadata", "url": REPO})
    assert error.value.code == "rate_limited"
    assert error.value.retry_after == 60


async def test_document_chrome_change_does_not_change_content_version() -> None:
    pages = iter(
        [
            "<html><body><nav>old</nav><main>Requires Python 3.11.</main></body></html>",
            "<html><body><nav>new</nav><main>Requires Python 3.11.</main></body></html>",
        ]
    )
    client = fetcher(
        lambda request: httpx.Response(200, text=next(pages), headers={"content-type": "text/html"})
    )
    source = {"kind": "document", "url": "https://docs.example/"}
    first = await client.fetch(source)
    second = await client.fetch(source, first.model_dump())
    assert first.version == second.version
    assert second.not_modified


async def test_fixture_release_records_do_not_require_live_api_access() -> None:
    client = FixtureFetcher()
    for repo in (
        "https://github.com/nano-muse/nanoMuse",
        "https://github.com/Anil-matcha/open-dots",
    ):
        result = await client.fetch({"kind": "releases", "url": repo})
        assert result.version.startswith("sha256:")
        assert result.url.endswith("/releases.atom")
        assert result.fetched_at


async def test_redirect_limit_bounds_requests() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(SourceError) as error:
        await fetcher(handler, max_redirects=2).fetch(
            {"kind": "document", "url": "https://docs.example/"}
        )
    assert error.value.code == "redirect_limit"
    assert len(requests) == 3


async def test_invalid_binary_content_type_is_rejected() -> None:
    with pytest.raises(SourceError) as error:
        await fetcher(
            lambda request: httpx.Response(
                200, content=b"binary", headers={"content-type": "application/pdf"}
            )
        ).fetch({"kind": "document", "url": "https://docs.example/"})
    assert error.value.code == "unsupported_media"


async def test_configured_token_is_only_sent_to_github_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NANOMUSE_RESEARCH_GITHUB_TOKEN", "synthetic-test-value")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "api.github.com":
            assert request.headers["authorization"] == "Bearer synthetic-test-value"
            return httpx.Response(200, json={"full_name": "research-example/sample"})
        assert "authorization" not in request.headers
        return httpx.Response(200, text="Public documentation")

    client = fetcher(handler)
    await client.fetch({"kind": "metadata", "url": REPO})
    await client.fetch({"kind": "document", "url": "https://docs.example/"})
