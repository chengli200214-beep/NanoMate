"""DoH cannot disable destination checks or inherit a proxy or credential."""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from nanomuse.research.sources import HttpSourceFetcher, SourceError
from nanomuse.research.sources.dns import CloudflareResolver, configured_resolver


def dns_reply(request: httpx.Request, **updates: Any) -> httpx.Response:
    kind = int(request.url.params["type"])
    data = {
        "Status": 0,
        "Question": [{"name": request.url.params["name"], "type": kind}],
        "Answer": [{"type": 1, "data": "93.184.216.34"}] if kind == 1 else None,
        **updates,
    }
    return httpx.Response(200, json=data)


async def test_doh_bootstrap_bypasses_system_dns_but_preserves_source_ip_pinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_system_dns(*_: Any, **__: Any) -> None:
        pytest.fail("DoH consulted the system resolver")

    monkeypatch.setattr(socket, "getaddrinfo", no_system_dns)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    requests = []

    def dns(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.scheme == "https" and request.url.host == "1.1.1.1"
        assert "authorization" not in request.headers
        return dns_reply(request)

    def source(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "docs.example"
        assert request.extensions["sni_hostname"] == "docs.example"
        return httpx.Response(200, text="Public documentation")

    fetcher = HttpSourceFetcher(
        resolver=CloudflareResolver(httpx.MockTransport(dns)),
        transport=httpx.MockTransport(source),
    )
    result = await fetcher.fetch({"kind": "document", "url": "https://docs.example/guide"})
    assert result.content == "Public documentation"
    assert {r.url.params["type"] for r in requests} == {"1", "28"}


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "198.18.0.1", "::1"])
async def test_private_or_synthetic_doh_answer_is_rejected(address: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return dns_reply(request, Answer=[{"type": 1, "data": address}])

    resolver = CloudflareResolver(httpx.MockTransport(handler))
    with pytest.raises(SourceError) as error:
        await resolver("docs.example", 443)
    assert error.value.code == "unsafe_url"


@pytest.mark.parametrize(
    "updates",
    [
        {"Status": 2},
        {"TC": True},
        {"Question": [{"name": "other.example", "type": 1}]},
        {"Answer": "invalid"},
        {"Answer": ["invalid"]},
        {"Answer": [{"type": 1, "data": None}]},
    ],
)
async def test_malformed_or_mismatched_dns_response_is_rejected(updates: Any) -> None:
    resolver = CloudflareResolver(httpx.MockTransport(lambda r: dns_reply(r, **updates)))
    with pytest.raises(SourceError):
        await resolver("docs.example", 443)


async def test_doh_redirect_is_not_followed() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/dns"})

    with pytest.raises(SourceError):
        await CloudflareResolver(httpx.MockTransport(handler))("docs.example", 443)
    assert all(r.url.host == "1.1.1.1" for r in calls)


async def test_oversized_dns_response_is_rejected() -> None:
    resolver = CloudflareResolver(
        httpx.MockTransport(lambda r: httpx.Response(200, text=" " * 65537))
    )
    with pytest.raises(SourceError, match="size limit"):
        await resolver("docs.example", 443)


def test_dns_is_explicit_opt_in_and_unknown_modes_do_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NANOMUSE_RESEARCH_DNS", raising=False)
    assert configured_resolver() is None
    monkeypatch.setenv("NANOMUSE_RESEARCH_DNS", "cloudflare")
    assert isinstance(configured_resolver(), CloudflareResolver)
    monkeypatch.setenv("NANOMUSE_RESEARCH_DNS", "invalid")
    with pytest.raises(ValueError, match="system or cloudflare"):
        configured_resolver()
