"""Public HTTP destinations, with the validated DNS answer pinned to the connection."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence

import httpx

from .errors import SourceError

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
_FORBIDDEN_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".lan", ".home")
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def _public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise SourceError("The resolver returned an invalid address.", code="unsafe_url") from exc
    if (
        not address.is_global
        or address.is_private
        or address.is_reserved
        or address.is_multicast
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv6Address) and address.is_site_local)
    ):
        raise SourceError("Only public Internet destinations are allowed.", code="unsafe_url")
    if isinstance(address, ipaddress.IPv6Address) and (
        address.ipv4_mapped is not None
        or address.sixtofour is not None
        or address.teredo is not None
        or any(address in network for network in _NAT64)
    ):
        raise SourceError("IP transition addresses are not accepted.", code="unsafe_url")
    return str(address)


def validate_url(value: str) -> httpx.URL:
    """Validate syntax and literal addresses without performing any network access."""
    if not value or re.search(r"[\x00-\x20\x7f\\]", value):
        raise SourceError(
            "Enter an HTTP or HTTPS URL without control characters.", code="unsafe_url"
        )
    try:
        url = httpx.URL(value)
        host = url.raw_host.decode("ascii").lower().rstrip(".")
        port = url.port
    except (httpx.InvalidURL, ValueError) as exc:
        raise SourceError("The source URL is invalid.", code="unsafe_url") from exc
    if url.scheme not in ("http", "https") or not host or url.userinfo or "%" in host:
        raise SourceError(
            "Only HTTP or HTTPS URLs without credentials are allowed.", code="unsafe_url"
        )
    if port is not None and not 1 <= port <= 65535:
        raise SourceError("The source port is invalid.", code="unsafe_url")
    if host == "localhost" or host.endswith(_FORBIDDEN_SUFFIXES):
        raise SourceError("Local hostnames are not allowed.", code="unsafe_url")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # A single name is often a machine on the local search domain. Numeric variants
        # such as decimal IPv4 must not acquire meaning from the system resolver.
        if "." not in host or re.fullmatch(
            r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host
        ):
            raise SourceError(
                "Use a public, fully qualified hostname.", code="unsafe_url"
            ) from None
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in host.split(".")
        ):
            raise SourceError("The source hostname is invalid.", code="unsafe_url") from None
    else:
        _public_address(host)
    return url.copy_with(host=host, fragment=None)


async def resolve_host(host: str, port: int) -> Sequence[str]:
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(answer[4][0]) for answer in answers))


class PublicTransport(httpx.AsyncBaseTransport):
    """An HTTP transport cannot resolve an already validated hostname a second time.

    The underlying transport receives an IP URL and the original Host and TLS SNI.
    Its environment proxies are disabled. Injected transports belong to their caller.
    """

    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None
    ) -> None:
        self._owned = transport is None
        self._transport = transport or httpx.AsyncHTTPTransport(
            trust_env=False,
            http2=False,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self._resolver = resolver or resolve_host

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = validate_url(str(request.url))
        try:
            ipaddress.ip_address(url.host)
        except ValueError:
            try:
                answers = await self._resolver(
                    url.host, url.port or (443 if url.scheme == "https" else 80)
                )
            except (OSError, TimeoutError) as exc:
                raise SourceError(
                    "The public hostname could not be resolved.", code="dns_error", retryable=True
                ) from exc
            if not answers:
                raise SourceError(
                    "The hostname has no addresses.", code="dns_error", retryable=True
                ) from None
            # Reject the whole DNS answer if any address could reach a private network.
            addresses = [_public_address(answer) for answer in answers]
            address = next((item for item in addresses if ":" not in item), addresses[0])
        else:
            address = _public_address(url.host)
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=address),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        return await self._transport.handle_async_request(pinned)

    async def aclose(self) -> None:
        if self._owned:
            await self._transport.aclose()
