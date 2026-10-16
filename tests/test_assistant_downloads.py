"""Public-link downloads prove file bytes and never replay an uncertain request."""

from __future__ import annotations

import asyncio
import hashlib
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from nanomuse.assistant.browser_actions import BrowserSpec, prepare_download
from nanomuse.assistant.browser_downloads import (
    MAX_DOWNLOAD_BYTES,
    BrowserDownloads,
    configured_download_resolver,
)
from nanomuse.assistant.file_actions import OutcomeUnknown

PUBLIC_IP = "93.184.216.34"


async def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


@pytest.fixture
def downloads(tmp_path: Path) -> BrowserDownloads:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # The transport stub receives an already pinned public IP. No test relaxes the
    # production URL/DNS validation, and no runtime flag can bypass that validation.
    return BrowserDownloads(tmp_path / "state", workspace, resolver=public_resolver)


def spec(downloads: BrowserDownloads, path: str = "article.bin") -> BrowserSpec:
    return prepare_download(
        {
            "action": "download",
            "index": 0,
            "path": path,
            "expected_url": "https://example.test/article",
            "observation_hash": "1" * 64,
        },
        downloads.workspace,
    )


async def execute(
    downloads: BrowserDownloads,
    transport: httpx.AsyncBaseTransport,
    value: BrowserSpec | None = None,
    check: Any = lambda: None,
    url: str = "https://example.test/article.bin",
) -> dict[str, Any]:
    return await downloads.execute(
        (value or spec(downloads)).model_dump(),
        url,
        "download-step-1",
        "task-1",
        1,
        "step-1",
        check,
        transport=transport,
    )


def test_preview_checks_workspace_destination_without_fetching(downloads: BrowserDownloads) -> None:
    value = spec(downloads)
    assert value.target_absent
    assert value.path == "article.bin"
    assert not (downloads.workspace / "article.bin").exists()
    assert downloads._row("download-step-1") is None
    (downloads.workspace / "article.bin").write_bytes(b"existing")
    with pytest.raises(ValueError, match="already exists"):
        spec(downloads)


@pytest.mark.parametrize("path", ["../outside", "C:/outside", "CON.txt", "a:b", "/tmp/x", "a./b"])
def test_unsafe_download_paths_are_rejected(downloads: BrowserDownloads, path: str) -> None:
    with pytest.raises(ValidationError):
        spec(downloads, path)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "download", "url": "https://example.test/a", "path": "a.bin"},
        {"action": "download", "index": 0, "path": "a.bin"},
        {"action": "navigate", "url": "https://example.test/", "path": "a.bin"},
        {"action": "extract", "target_absent": True},
    ],
)
def test_download_requires_observed_link_and_only_download_accepts_path(payload: Any) -> None:
    with pytest.raises(ValidationError):
        BrowserSpec.model_validate(payload)


async def test_binary_bytes_saved_with_hash_and_no_browser_cookies(
    downloads: BrowserDownloads,
) -> None:
    requests: list[httpx.Request] = []
    body = b"\x00\xff\x80PDF-like bytes\n"

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": "application/octet-stream",
                "content-disposition": 'attachment; filename="../../outside.exe"',
            },
        )

    value = spec(downloads)
    result = await execute(downloads, httpx.MockTransport(respond), value)
    assert (downloads.workspace / "article.bin").read_bytes() == body
    assert result["verification"] == {
        "kind": "file_sha256",
        "path": "article.bin",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "content_type": "application/octet-stream",
    }
    assert result["cookies_sent"] is False
    assert result["effective_action"] == "public_get"
    assert len(requests) == 1
    assert not list(downloads.workspace.glob(".assistant-download-*"))
    restarted = BrowserDownloads(
        downloads.path.parent, downloads.workspace, resolver=public_resolver
    )
    assert restarted.recover(value.model_dump(), "download-step-1", "task-1", 1, "step-1") == result
    # The exact same request key acknowledges already existing bytes, not another GET.
    assert await execute(restarted, httpx.MockTransport(respond), value) == result
    assert len(requests) == 1


async def test_same_origin_redirect_drops_even_newly_set_cookies(
    downloads: BrowserDownloads,
) -> None:
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert "cookie" not in request.headers
        if len(paths) == 1:
            return httpx.Response(
                302, headers={"location": "/final.bin", "set-cookie": "tracking=private"}
            )
        return httpx.Response(200, content=b"final")

    result = await execute(downloads, httpx.MockTransport(respond))
    assert paths == ["/article.bin", "/final.bin"]
    assert result["operation"]["final_url"] == "https://example.test/final.bin"


@pytest.mark.parametrize(
    "destination", ["https://other.test/file", "http://example.test/file", "http://127.0.0.1/"]
)
async def test_cross_origin_redirect_never_sends_second_request(
    downloads: BrowserDownloads, destination: str
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": destination})

    with pytest.raises(ValueError, match="another origin|unsafe"):
        await execute(downloads, httpx.MockTransport(respond))
    assert len(calls) == 1
    assert not (downloads.workspace / "article.bin").exists()


async def test_private_guard_blocks_before_any_request(
    downloads: BrowserDownloads,
) -> None:
    hosts: list[str] = []

    async def private(host: str, port: int) -> list[str]:
        hosts.append(host)
        return ["127.0.0.1"]

    downloads._resolver = private

    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("private request escaped the guard")

    with pytest.raises(ValueError, match="public Internet"):
        await execute(downloads, httpx.MockTransport(never))
    assert hosts == ["example.test"]
    assert downloads.cancel_evidence("download-step-1")[0] == "cancelled"


async def test_private_guard_rechecks_each_redirect(
    downloads: BrowserDownloads,
) -> None:
    checks: list[str] = []
    requests: list[str] = []

    async def private(host: str, port: int) -> list[str]:
        checks.append(host)
        return [PUBLIC_IP] if len(checks) == 1 else ["127.0.0.1"]

    downloads._resolver = private

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "/final"})

    with pytest.raises(ValueError, match="public Internet"):
        await execute(downloads, httpx.MockTransport(respond))
    assert checks == ["example.test", "example.test"]
    assert len(requests) == 1


class Chunks(httpx.AsyncByteStream):
    def __init__(self, values: list[bytes], after: Any = None):
        self.values = values
        self.after = after

    async def __aiter__(self) -> Any:
        for value in self.values:
            yield value
            if self.after is not None:
                self.after()


@pytest.mark.parametrize("declared", [str(MAX_DOWNLOAD_BYTES + 1), "-1", "bad"])
async def test_invalid_or_oversize_length_never_publishes(
    downloads: BrowserDownloads, declared: str
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, stream=Chunks([b"data"]), headers={"content-length": declared}
        )
    )
    with pytest.raises(ValueError):
        await execute(downloads, transport)
    assert not (downloads.workspace / "article.bin").exists()
    assert not list(downloads.workspace.glob(".assistant-download-*"))


async def test_unknown_length_is_capped_before_publication(downloads: BrowserDownloads) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Chunks([b"x" * 65536] * 257))
    )
    with pytest.raises(ValueError, match="size limit"):
        await execute(downloads, transport)
    assert not (downloads.workspace / "article.bin").exists()


@pytest.mark.parametrize(
    "status,headers,body",
    [
        (200, {"content-length": "10"}, b"short"),
        (200, {"content-encoding": "gzip"}, b""),
        (206, {}, b"partial"),
        (404, {}, b"missing"),
    ],
)
async def test_incomplete_compressed_and_error_responses_are_not_files(
    downloads: BrowserDownloads, status: int, headers: dict[str, str], body: bytes
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, headers=headers, stream=Chunks([body]))
    )
    with pytest.raises(ValueError):
        await execute(downloads, transport)
    assert not (downloads.workspace / "article.bin").exists()


async def test_redirect_loop_is_bounded(downloads: BrowserDownloads) -> None:
    calls = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(ValueError, match="redirect limit"):
        await execute(downloads, httpx.MockTransport(respond))
    assert calls == 6


async def test_collision_after_preview_never_overwrites_or_fetches(
    downloads: BrowserDownloads,
) -> None:
    value = spec(downloads)
    (downloads.workspace / "article.bin").write_bytes(b"user file")

    def never(_: httpx.Request) -> httpx.Response:
        raise AssertionError("destination collision must stop before fetching")

    with pytest.raises(ValueError, match="already exists"):
        await execute(downloads, httpx.MockTransport(never), value)
    assert (downloads.workspace / "article.bin").read_bytes() == b"user file"
    assert downloads._row("download-step-1") is None


async def test_collision_during_download_keeps_user_file(downloads: BrowserDownloads) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        (downloads.workspace / "article.bin").write_bytes(b"created during request")
        return httpx.Response(200, content=b"download")

    with pytest.raises(OutcomeUnknown, match="publication is unverified"):
        await execute(downloads, httpx.MockTransport(respond))
    assert (downloads.workspace / "article.bin").read_bytes() == b"created during request"
    assert downloads.cancel_evidence("download-step-1")[0] == "outcome_unknown"


async def test_interrupted_request_is_never_repeated(downloads: BrowserDownloads) -> None:
    value = spec(downloads)
    calls = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("fixture connection lost", request=request)

    transport = httpx.MockTransport(fail)
    with pytest.raises(ValueError, match="before publication"):
        await execute(downloads, transport, value)
    with pytest.raises(OutcomeUnknown, match="review a new plan"):
        await execute(downloads, transport, value)
    assert calls == 1
    assert downloads.cancel_evidence("download-step-1") == (
        "cancelled",
        {"cancelled_before_publication": True},
    )


async def test_pause_between_chunks_prevents_publication(downloads: BrowserDownloads) -> None:
    running = True

    def check() -> None:
        if not running:
            raise ValueError("task paused")

    def pause() -> None:
        nonlocal running
        running = False

    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Chunks([b"first" * 65536, b"later"], pause))
    )
    with pytest.raises(ValueError, match="task paused"):
        await execute(downloads, transport, check=check)
    assert not (downloads.workspace / "article.bin").exists()
    assert not list(downloads.workspace.glob(".assistant-download-*"))


async def test_published_file_recovers_after_crash_without_another_get(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Crash(BaseException):
        pass

    value = spec(downloads)
    original = downloads._complete
    monkeypatch.setattr(downloads, "_complete", lambda _: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        await execute(
            downloads, httpx.MockTransport(lambda _: httpx.Response(200, content=b"done")), value
        )
    assert (downloads.workspace / "article.bin").read_bytes() == b"done"
    assert downloads._row("download-step-1")["state"] == "publishing"  # type: ignore[index]
    monkeypatch.setattr(downloads, "_complete", original)
    result = downloads.recover(value.model_dump(), "download-step-1", "task-1", 1, "step-1")
    assert (
        result is not None
        and result["verification"]["sha256"] == hashlib.sha256(b"done").hexdigest()
    )
    assert downloads.cancel_evidence("download-step-1")[0] == "completed"


async def test_recovery_rejects_changed_file_and_reused_request_key(
    downloads: BrowserDownloads,
) -> None:
    value = spec(downloads)
    await execute(
        downloads, httpx.MockTransport(lambda _: httpx.Response(200, content=b"done")), value
    )
    with pytest.raises(ValueError, match="reused"):
        downloads.recover(value.model_dump(), "download-step-1", "another-task", 1, "step-1")
    (downloads.workspace / "article.bin").write_bytes(b"changed")
    with pytest.raises(OutcomeUnknown, match="unavailable or changed"):
        downloads.recover(value.model_dump(), "download-step-1", "task-1", 1, "step-1")
    assert downloads.cancel_evidence("download-step-1")[0] == "outcome_unknown"


async def test_identical_replacement_bytes_do_not_pass_identity_verification(
    downloads: BrowserDownloads,
) -> None:
    value = spec(downloads)
    await execute(
        downloads, httpx.MockTransport(lambda _: httpx.Response(200, content=b"done")), value
    )
    target = downloads.workspace / "article.bin"
    replacement = downloads.workspace / "replacement.bin"
    replacement.write_bytes(b"done")
    replacement.replace(target)
    with pytest.raises(OutcomeUnknown):
        downloads.recover(value.model_dump(), "download-step-1", "task-1", 1, "step-1")


async def test_unprepared_destination_never_requests(downloads: BrowserDownloads) -> None:
    value = spec(downloads).model_copy(update={"target_absent": False})
    with pytest.raises(ValueError, match="prepared"):
        await execute(downloads, httpx.MockTransport(lambda _: httpx.Response(200)), value)
    assert downloads._row("download-step-1") is None


async def test_slow_stream_has_total_deadline_and_no_automatic_retry(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nanomuse.assistant.browser_downloads.MAX_DOWNLOAD_SECONDS", 0.02)
    calls = 0

    async def delayed(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return httpx.Response(200, content=b"late")

    value = spec(downloads)
    transport = httpx.MockTransport(delayed)
    with pytest.raises(ValueError, match="before publication"):
        await execute(downloads, transport, value)
    with pytest.raises(OutcomeUnknown):
        await execute(downloads, transport, value)
    assert calls == 1
    assert not (downloads.workspace / "article.bin").exists()
    assert not list(downloads.workspace.glob(".assistant-download-*"))


async def test_system_dns_is_pinned_without_a_second_hostname_lookup(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolutions: list[tuple[str, int]] = []
    requests: list[httpx.Request] = []

    async def system_resolver(host: str, port: int) -> list[str]:
        resolutions.append((host, port))
        return [PUBLIC_IP]

    def never_resolve_again(*_: Any, **__: Any) -> Any:
        pytest.fail("The validated hostname was resolved a second time")

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == PUBLIC_IP
        assert request.headers["host"] == "example.test"
        assert request.extensions["sni_hostname"] == "example.test"
        return httpx.Response(200, content=b"pinned public bytes")

    downloads._resolver = None  # The production default selects the system resolver.
    monkeypatch.setattr("nanomuse.research.sources.security.resolve_host", system_resolver)
    monkeypatch.setattr(socket, "getaddrinfo", never_resolve_again)
    result = await execute(downloads, httpx.MockTransport(respond))
    assert resolutions == [("example.test", 443)]
    assert len(requests) == 1
    assert result["operation"]["final_url"] == "https://example.test/article.bin"


async def test_mixed_public_private_dns_answers_never_reach_transport(
    downloads: BrowserDownloads,
) -> None:
    async def mixed(host: str, port: int) -> list[str]:
        return [PUBLIC_IP, "127.0.0.1"]

    def never(_: httpx.Request) -> httpx.Response:
        pytest.fail("A mixed public/private DNS answer reached the transport")

    downloads._resolver = mixed
    with pytest.raises(ValueError, match="public Internet"):
        await execute(downloads, httpx.MockTransport(never))
    assert not (downloads.workspace / "article.bin").exists()


async def test_literal_private_address_is_rejected_before_intent_or_dns(
    downloads: BrowserDownloads,
) -> None:
    async def never_resolve(host: str, port: int) -> list[str]:
        pytest.fail("A private literal must not be sent to any DNS resolver")

    downloads._resolver = never_resolve
    with pytest.raises(ValueError, match="unsafe"):
        await execute(
            downloads,
            httpx.MockTransport(lambda _: httpx.Response(200)),
            url="http://127.0.0.1/file",
        )
    assert downloads._row("download-step-1") is None


def test_research_dns_opt_in_is_not_inherited_by_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NANOMUSE_RESEARCH_DNS", "cloudflare")

    def forbidden() -> Any:
        pytest.fail("Research DNS approval must not opt browser downloads into Cloudflare")

    monkeypatch.setattr("nanomuse.assistant.browser_downloads.CloudflareResolver", forbidden)
    assert configured_download_resolver() is None


async def test_explicit_assistant_cloudflare_mode_uses_pinned_resolver_only(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolutions: list[tuple[str, int]] = []

    async def cloudflare(host: str, port: int) -> list[str]:
        resolutions.append((host, port))
        return [PUBLIC_IP]

    monkeypatch.setenv("NANOMUSE_ASSISTANT_DNS", " Cloudflare ")
    monkeypatch.setattr(
        "nanomuse.assistant.browser_downloads.CloudflareResolver", lambda: cloudflare
    )
    configured = BrowserDownloads(downloads.path.parent, downloads.workspace)
    assert configured._resolver is cloudflare

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == PUBLIC_IP
        assert request.headers["host"] == "example.test"
        return httpx.Response(200, content=b"explicit resolver")

    await execute(configured, httpx.MockTransport(respond))
    assert resolutions == [("example.test", 443)]


@pytest.mark.parametrize("mode", ["", "auto", "fallback", "https://resolver.example/"])
def test_invalid_assistant_dns_mode_fails_without_creating_state(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("NANOMUSE_ASSISTANT_DNS", mode)
    state = downloads.path.parent / "invalid-setting"
    with pytest.raises(ValueError, match="must be system or cloudflare"):
        BrowserDownloads(state, downloads.workspace)
    assert not state.exists()


async def test_system_dns_private_answer_has_no_automatic_cloudflare_fallback(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def private(host: str, port: int) -> list[str]:
        return ["198.18.0.1"]

    def forbidden() -> Any:
        pytest.fail("System DNS failure must not implicitly send hostnames to Cloudflare")

    monkeypatch.setenv("NANOMUSE_ASSISTANT_DNS", "system")
    monkeypatch.setattr("nanomuse.research.sources.security.resolve_host", private)
    monkeypatch.setattr("nanomuse.assistant.browser_downloads.CloudflareResolver", forbidden)
    configured = BrowserDownloads(downloads.path.parent, downloads.workspace)
    assert configured._resolver is None
    with pytest.raises(ValueError, match="public Internet"):
        await execute(configured, httpx.MockTransport(lambda _: pytest.fail("unsafe DNS escaped")))
    assert not (downloads.workspace / "article.bin").exists()


async def test_injected_transport_remains_owned_by_the_test_caller(
    downloads: BrowserDownloads,
) -> None:
    class Transport(httpx.AsyncBaseTransport):
        closed = False

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.url.host == PUBLIC_IP
            return httpx.Response(200, content=b"injected transport")

        async def aclose(self) -> None:
            self.closed = True

    transport = Transport()
    await execute(downloads, transport)
    assert not transport.closed
    await transport.aclose()
    assert transport.closed


async def test_default_transport_is_pinned_and_closed_by_download(
    downloads: BrowserDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Transport(httpx.AsyncBaseTransport):
        closed = False

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.url.host == PUBLIC_IP
            assert request.headers["host"] == "example.test"
            return httpx.Response(200, content=b"owned default transport")

        async def aclose(self) -> None:
            self.closed = True

    transport = Transport()
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: transport)
    await downloads.execute(
        spec(downloads).model_dump(),
        "https://example.test/article.bin",
        "download-step-1",
        "task-1",
        1,
        "step-1",
        lambda: None,
    )
    assert transport.closed
