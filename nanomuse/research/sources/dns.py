"""Optional public DNS over HTTPS for networks that return synthetic local addresses."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from typing import Any

import httpx

from .errors import SourceError
from .security import Resolver, _public_address, validate_url

DNS_MODES = ("system", "cloudflare")
_ENDPOINT = "https://1.1.1.1/dns-query"
_MAX_BYTES = 65_536


class CloudflareResolver:
    """Use a fixed HTTPS IP endpoint, without system DNS, redirects or environment proxies.

    Opting in sends registered source hostnames to Cloudflare. Both address families
    are checked. PublicTransport still validates and pins the returned addresses.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def __call__(self, host: str, port: int) -> Sequence[str]:
        # Never turn an arbitrary string into a DNS query, even when called directly.
        host = validate_url(f"https://{host}:{port}/").host
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=5,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                groups = await asyncio.gather(
                    self._query(client, host, 1), self._query(client, host, 28)
                )
        except (httpx.HTTPError, OSError, TimeoutError, ValueError) as exc:
            raise SourceError(
                "Public DNS over HTTPS failed. Check connectivity or select system DNS.",
                code="dns_error",
                retryable=True,
            ) from exc
        addresses = list(dict.fromkeys(address for group in groups for address in group))
        if not addresses:
            raise SourceError("The hostname has no public addresses.", code="dns_error")
        return addresses

    async def _query(self, client: httpx.AsyncClient, host: str, kind: int) -> list[str]:
        async with client.stream(
            "GET",
            _ENDPOINT,
            params={"name": host, "type": str(kind)},
            headers={"Accept": "application/dns-json", "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise SourceError(
                    "Public DNS over HTTPS did not return a successful response.",
                    code="dns_error",
                    retryable=True,
                )
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                body.extend(chunk)
                if len(body) > _MAX_BYTES:
                    raise SourceError("The DNS response exceeds the size limit.", code="dns_error")
        data: Any = json.loads(body)
        if not isinstance(data, dict) or data.get("Status") != 0 or data.get("TC"):
            raise SourceError("The public DNS response could not be used.", code="dns_error")
        question = data.get("Question")
        if not (
            isinstance(question, list)
            and len(question) == 1
            and isinstance(question[0], dict)
            and str(question[0].get("name", "")).lower().rstrip(".") == host
            and question[0].get("type") == kind
        ):
            raise SourceError("The DNS response did not match the query.", code="dns_error")
        answers = data.get("Answer", [])
        if answers is None:
            answers = []
        if not isinstance(answers, list) or not all(isinstance(a, dict) for a in answers):
            raise SourceError("The public DNS answer is invalid.", code="dns_error")
        addresses = []
        for answer in answers:
            if answer.get("type") in (1, 28):
                value = answer.get("data")
                if not isinstance(value, str):
                    raise SourceError("The DNS address is invalid.", code="dns_error")
                addresses.append(_public_address(value))
        return addresses


def configured_resolver() -> Resolver | None:
    """The resolver is an operator setting, never controlled by source content."""
    mode = os.environ.get("NANOMUSE_RESEARCH_DNS", "system").strip().lower()
    if mode == "system":
        return None
    if mode == "cloudflare":
        return CloudflareResolver()
    raise ValueError("NANOMUSE_RESEARCH_DNS must be system or cloudflare.")
