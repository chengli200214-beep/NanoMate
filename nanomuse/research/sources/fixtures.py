"""Explicit file-backed source replay. This adapter never sends an HTTP request."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nanomuse.research.clock import SystemClock, iso
from nanomuse.research.contracts import Clock, FetchedSource

from .errors import SourceError
from .http import HttpSourceFetcher, content_version
from .security import Resolver, _public_address, validate_url


class FixtureFetcher:
    """Replay exact source-ID, ``kind:url`` or URL keys from a mapping or JSON file.

    No network or DNS is used by default. URL syntax and literal/private destinations
    are still checked, and the source must have an explicitly registered fixture key.
    A caller can inject a resolver to additionally check saved hostname resolutions.
    With no argument, use the bundled, attributed public-source excerpts.
    """

    fixture = True

    def __init__(
        self,
        fixtures: Mapping[str, Any] | str | Path | None = None,
        *,
        clock: Clock | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._resolver = resolver
        if fixtures is None:
            fixtures = Path(__file__).parent / "fixtures" / "public-sources.json"
        if isinstance(fixtures, (str, Path)):
            try:
                payload = json.loads(Path(fixtures).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SourceError(
                    "The fixture file could not be read.", code="invalid_fixture"
                ) from exc
            if not isinstance(payload, dict) or payload.get("fixture") is not True:
                raise SourceError(
                    "The source file must explicitly declare fixture mode.", code="invalid_fixture"
                )
            fixtures = payload.get("sources", {})
        if not isinstance(fixtures, Mapping):
            raise SourceError("Fixture sources must be a mapping.", code="invalid_fixture")
        self._fixtures = dict(fixtures)

    async def _validate(self, value: str) -> None:
        url = validate_url(value)
        if self._resolver:
            answers = await self._resolver(
                url.host, url.port or (443 if url.scheme == "https" else 80)
            )
            if not answers:
                raise SourceError("The fixture hostname has no addresses.", code="unsafe_url")
            for answer in answers:
                _public_address(answer)

    async def fetch(
        self, source: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> FetchedSource:
        url = str(source.get("url", ""))
        if url:
            await self._validate(url)
        kind = str(source.get("kind", "readme"))
        if kind == "manual":
            return await HttpSourceFetcher(clock=self._clock).fetch(source, previous)
        if not url:
            raise SourceError("A fixture source requires a public URL.", code="unsafe_url")
        keys = (str(source.get("id", "")), f"{kind}:{url}", url)
        record = next((self._fixtures[key] for key in keys if key and key in self._fixtures), None)
        if record is None:
            raise SourceError(
                "No fixed snapshot is registered for this source.", code="fixture_missing"
            )
        if isinstance(record, FetchedSource):
            result = record.model_copy(deep=True)
        else:
            try:
                fields = {key: record[key] for key in FetchedSource.model_fields if key in record}
                fields.setdefault("url", url)
                fields.setdefault("title", str(source.get("title", "")))
                result = FetchedSource.model_validate(fields)
            except (TypeError, KeyError, ValidationError) as exc:
                raise SourceError(
                    "The fixed snapshot has an invalid shape.", code="invalid_fixture"
                ) from exc
        await self._validate(result.url)
        if len(result.content) > 200_000 or len(result.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise SourceError("The fixed snapshot exceeds the size limit.", code="size_limit")
        if not result.version:
            result.version = content_version(result.content)
        if not result.fetched_at:
            result.fetched_at = iso(self._clock.now())
        result.not_modified = bool(previous and previous.get("version") == result.version)
        return result
