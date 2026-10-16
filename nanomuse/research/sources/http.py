"""Read registered GitHub and documentation sources, never execute repository code."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import os
import re
import ssl
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
from bs4 import BeautifulSoup, Comment
from html2text import HTML2Text

from nanomuse.research.clock import SystemClock, iso
from nanomuse.research.contracts import Clock, FetchedSource

from .dns import configured_resolver
from .errors import SourceError
from .security import PublicTransport, Resolver, validate_url

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_CONTENT_CHARS = 200_000
MAX_REDIRECTS = 5
RELEASE_PAGE_SIZE = 5
_SHA = re.compile(r"[0-9a-f]{40}")


def content_version(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Repository:
    owner: str
    name: str
    ref: str = "HEAD"
    path: str = ""

    @property
    def api(self) -> str:
        return f"https://api.github.com/repos/{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def _repository(url: str, *, file_required: bool = False) -> _Repository:
    parsed = validate_url(url)
    pieces = urlsplit(str(parsed)).path.strip("/").split("/")
    if parsed.host in ("github.com", "www.github.com"):
        offset = 3 if len(pieces) > 2 and pieces[2] in ("blob", "raw") else 0
    elif parsed.host == "raw.githubusercontent.com":
        offset = 2
    elif parsed.host == "api.github.com" and pieces[0] == "repos":
        pieces = pieces[1:]
        offset = 0
    else:
        raise SourceError(
            "This source must point to a public GitHub repository.", code="invalid_source"
        )
    if len(pieces) < 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in pieces[:2]):
        raise SourceError("The GitHub repository URL is invalid.", code="invalid_source")
    owner, name = pieces[:2]
    name = name.removesuffix(".git")
    if not name or owner in (".", "..") or name in (".", ".."):
        raise SourceError("The GitHub repository URL is invalid.", code="invalid_source")
    if offset:
        if len(pieces) <= offset + 1:
            raise SourceError(
                "Use a GitHub file URL containing a ref and a path.", code="invalid_source"
            )
        ref = unquote(pieces[offset])
        path = "/".join(unquote(piece) for piece in pieces[offset + 1 :])
        if (
            not ref
            or ref in (".", "..")
            or "\\" in ref + path
            or any(p in ("", ".", "..") for p in path.split("/"))
        ):
            raise SourceError("The GitHub file path is invalid.", code="invalid_source")
        if re.search(r"[\x00-\x1f\x7f]", ref + path):
            raise SourceError("The GitHub file path is invalid.", code="invalid_source")
        return _Repository(owner, name, ref, path)
    if file_required:
        raise SourceError("Add the exact GitHub blob or raw file URL.", code="invalid_source")
    if len(pieces) > 2 and pieces[2:] != ["releases"]:
        raise SourceError("Use the repository URL or an explicit file URL.", code="invalid_source")
    return _Repository(owner, name)


def _document_text(body: str) -> tuple[str, str]:
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "iframe",
            "nav",
            "footer",
            "aside",
            "button",
            "form",
            "template",
        ]
    ):
        tag.decompose()
    for header in soup.find_all("header"):
        if header.find_parent(["main", "article"]) is None:
            header.decompose()
    for comment in soup.find_all(string=lambda item: isinstance(item, Comment)):
        comment.extract()
    for hidden in soup.select(
        '[hidden], [aria-hidden="true"], .last-updated, .last_updated, .updated-at, [data-last-updated]'
    ):
        hidden.decompose()
    for tag in soup.find_all(style=True):
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", str(tag["style"]), re.I):
            tag.decompose()
    for updated_label in soup.find_all(
        string=re.compile(r"^\s*(?:last\s+updated|updated\s+at|最后更新|更新时间)\s*[:：]", re.I)
    ):
        parent = updated_label.parent
        if (
            parent
            and parent.name in {"p", "span", "time"}
            and len(parent.get_text(" ", strip=True)) < 150
        ):
            parent.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    converter = HTML2Text()
    converter.ignore_images = True
    converter.body_width = 0
    converter.skip_internal_links = True
    text = converter.handle(str(main))
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return title, re.sub(r"\n{3,}", "\n\n", text)


class HttpSourceFetcher:
    """SourceFetcher for explicitly registered public sources.

    ``transport`` receives pinned IP URLs with the original Host and SNI. Pass an
    asynchronous ``resolver(host, port)`` for offline tests. Tokens are only sent to
    https://api.github.com and are never included in errors or persisted content.
    """

    def __init__(
        self,
        *,
        github_token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        clock: Clock | None = None,
        timeout: float = 30.0,
        max_body_bytes: int = MAX_BODY_BYTES,
        max_content_chars: int = MAX_CONTENT_CHARS,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._token = (
            os.environ.get("NANOMUSE_RESEARCH_GITHUB_TOKEN", "")
            if github_token is None
            else github_token
        )
        self._transport = transport
        self._resolver = resolver if resolver is not None else configured_resolver()
        self._clock = clock or SystemClock()
        self._timeout = timeout
        self._max_bytes = max_body_bytes
        self._max_chars = max_content_chars
        self._max_redirects = max_redirects
        if timeout <= 0 or max_body_bytes <= 0 or max_content_chars <= 0 or max_redirects < 0:
            raise ValueError("Source timeout and size limits must be positive.")

    async def fetch(
        self, source: dict[str, Any], previous: dict[str, Any] | None = None
    ) -> FetchedSource:
        kind = str(source.get("kind", "readme"))
        url = str(source.get("url", ""))
        if kind == "manual":
            if url:
                validate_url(url)
            content = self._limit(str(source.get("content", "")).strip())
            if not content:
                raise SourceError("Add Markdown or plain text to this source.", code="empty_source")
            version = content_version(content)
            return FetchedSource(
                url=url,
                title=str(source.get("title", "")),
                content=content,
                version=version,
                fetched_at=iso(self._clock.now()),
                not_modified=bool(previous and previous.get("version") == version),
            )
        validate_url(url)
        transport = PublicTransport(self._transport, self._resolver)
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"User-Agent": "nanoMuse-research/1", "Accept-Encoding": "identity"},
        ) as client:
            if kind == "document":
                return await self._document(client, source, previous)
            if kind not in ("readme", "file", "metadata", "releases"):
                raise SourceError("This source kind is not supported.", code="invalid_source")
            repo = _repository(url, file_required=kind == "file")
            return await self._github(client, source, repo, previous)

    def _limit(self, content: str) -> str:
        if len(content) > self._max_chars or len(content.encode("utf-8")) > self._max_bytes:
            raise SourceError("The source exceeds the content size limit.", code="size_limit")
        return content

    def _unchanged(
        self, source: dict[str, Any], previous: dict[str, Any] | None, etag: str
    ) -> FetchedSource:
        if not previous or "content" not in previous:
            raise SourceError("A 304 response requires a saved snapshot.", code="invalid_response")
        return FetchedSource(
            url=str(previous.get("url") or source.get("url", "")),
            title=str(previous.get("title") or source.get("title", "")),
            content=self._limit(str(previous["content"])),
            version=str(previous.get("version", "")),
            etag=etag or str(previous.get("etag", "")),
            fetched_at=iso(self._clock.now()),
            not_modified=True,
        )

    def _retry_after(self, response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after", "")
        if value:
            try:
                seconds = float(value)
                return max(0.0, seconds) if math.isfinite(seconds) else None
            except ValueError:
                try:
                    instant = parsedate_to_datetime(value)
                    if instant.tzinfo is None:
                        instant = instant.replace(tzinfo=UTC)
                    return max(0.0, instant.timestamp() - self._clock.now())
                except (TypeError, ValueError, OverflowError):
                    pass
        if response.headers.get("x-ratelimit-remaining") == "0":
            try:
                seconds = float(response.headers["x-ratelimit-reset"]) - self._clock.now()
                return max(0.0, seconds) if math.isfinite(seconds) else None
            except (KeyError, ValueError, OverflowError):
                pass
        return None

    async def _get(
        self, client: httpx.AsyncClient, url: str, *, etag: str = "", accept: str = ""
    ) -> httpx.Response:
        try:
            return await asyncio.wait_for(
                self._download(client, url, etag=etag, accept=accept), self._timeout
            )
        except TimeoutError as exc:
            raise SourceError(
                "The source request timed out.", code="timeout", retryable=True
            ) from exc

    async def _download(
        self, client: httpx.AsyncClient, url: str, *, etag: str = "", accept: str = ""
    ) -> httpx.Response:
        current = validate_url(url)
        headers: dict[str, str] = (
            {"Accept": "application/vnd.github+json"} if current.host == "api.github.com" else {}
        )
        if accept:
            headers["Accept"] = accept
        if self._token and current.host == "api.github.com" and current.scheme == "https":
            headers["Authorization"] = f"Bearer {self._token}"
        if etag:
            headers["If-None-Match"] = etag
        try:
            for hop in range(self._max_redirects + 1):
                async with client.stream("GET", current, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if hop == self._max_redirects or not response.headers.get("location"):
                            raise SourceError(
                                "The source redirected too many times.", code="redirect_limit"
                            )
                        destination = validate_url(str(current.join(response.headers["location"])))
                        if current.scheme == "https" and destination.scheme != "https":
                            raise SourceError(
                                "An HTTPS source cannot redirect to HTTP.", code="unsafe_url"
                            )
                        if (destination.scheme, destination.host, destination.port) != (
                            current.scheme,
                            current.host,
                            current.port,
                        ):
                            headers.pop("Authorization", None)
                            headers.pop("If-None-Match", None)
                        current = destination
                        continue
                    if response.status_code == 304:
                        return httpx.Response(
                            304, headers=response.headers, request=response.request
                        )
                    self._check_status(response)
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise SourceError(
                            "The source ignored the uncompressed text request.",
                            code="unsupported_media",
                        )
                    try:
                        declared = int(response.headers.get("content-length", "0"))
                    except ValueError:
                        declared = 0
                    if declared > self._max_bytes:
                        raise SourceError(
                            "The source exceeds the download size limit.", code="size_limit"
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > self._max_bytes:
                            raise SourceError(
                                "The source exceeds the download size limit.", code="size_limit"
                            )
                        body.extend(chunk)
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=bytes(body),
                        request=response.request,
                    )
        except httpx.TimeoutException as exc:
            raise SourceError(
                "The source request timed out.", code="timeout", retryable=True
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            cause: BaseException | None = exc
            certificate_failure = False
            for _ in range(10):
                if cause is None:
                    break
                certificate_failure |= isinstance(cause, ssl.SSLCertVerificationError)
                certificate_failure |= "CERTIFICATE_VERIFY_FAILED" in str(cause)
                cause = cause.__cause__ or cause.__context__
            raise SourceError(
                "The source request failed.",
                code="certificate_error" if certificate_failure else "network_error",
                retryable=not certificate_failure,
            ) from exc
        raise SourceError("No source response was received.", code="invalid_response")

    def _check_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        after = self._retry_after(response)
        if status == 429 or (
            status == 403
            and (after is not None or response.headers.get("x-ratelimit-remaining") == "0")
        ):
            raise SourceError(
                "The source rate limit was reached.",
                code="rate_limited",
                retryable=True,
                retry_after=after,
            )
        code = {401: "authentication", 403: "forbidden", 404: "not_found"}.get(status, "http_error")
        raise SourceError(
            f"The source returned HTTP {status}.",
            code=code,
            retryable=status in (408, 425) or 500 <= status < 600,
            retry_after=after,
        )

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError("The source returned invalid JSON.", code="invalid_response") from exc

    async def _github(
        self,
        client: httpx.AsyncClient,
        source: dict[str, Any],
        repo: _Repository,
        previous: dict[str, Any] | None,
    ) -> FetchedSource:
        kind = str(source.get("kind", "readme"))
        old_etag = str(previous.get("etag", "")) if previous else ""
        if kind == "file" and repo.path and _SHA.fullmatch(repo.ref):
            # The owner already supplied an immutable commit. Reading its public raw
            # file needs no API ref resolution, contents request, or GitHub token.
            endpoint = (
                f"https://raw.githubusercontent.com/{repo.owner}/{repo.name}/"
                f"{repo.ref}/{quote(repo.path, safe='/')}"
            )
            response = await self._get(client, endpoint, etag=old_etag)
            if response.status_code == 304:
                return self._unchanged(source, previous, response.headers.get("etag", ""))
            try:
                content = response.content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise SourceError(
                    "The GitHub file is not UTF-8 text.", code="invalid_content"
                ) from exc
            if "\x00" in content:
                raise SourceError("Binary files are not supported.", code="invalid_content")
            content = self._limit(content.strip())
            if not content:
                raise SourceError("The GitHub file is empty.", code="empty_source")
            return FetchedSource(
                url=f"{repo.url}/blob/{repo.ref}/{quote(repo.path, safe='/')}",
                title=str(source.get("title") or f"{repo.owner}/{repo.name}: {repo.path}"),
                content=content,
                version=repo.ref,
                etag=response.headers.get("etag", ""),
                fetched_at=iso(self._clock.now()),
            )
        if kind in ("metadata", "releases"):
            endpoint = repo.api + (
                f"/releases?per_page={RELEASE_PAGE_SIZE}" if kind == "releases" else ""
            )
            response = await self._get(client, endpoint, etag=old_etag)
            if response.status_code == 304:
                return self._unchanged(source, previous, response.headers.get("etag", ""))
            data = self._json(response)
            if kind == "metadata":
                if not isinstance(data, dict):
                    raise SourceError(
                        "Repository metadata is not an object.", code="invalid_response"
                    )
                keys: tuple[str, ...] = (
                    "full_name",
                    "description",
                    "html_url",
                    "default_branch",
                    "license",
                    "archived",
                    "disabled",
                    "visibility",
                    "language",
                    "topics",
                )
                data = {key: data[key] for key in keys if key in data}
                license_info = data.get("license")
                if isinstance(license_info, dict):
                    data["license"] = {key: license_info.get(key) for key in ("spdx_id", "name")}
            elif not isinstance(data, list):
                raise SourceError("The releases response is not a list.", code="invalid_response")
            else:
                keys = (
                    "id",
                    "tag_name",
                    "name",
                    "html_url",
                    "target_commitish",
                    "published_at",
                    "prerelease",
                    "body",
                )
                data = [
                    {key: release[key] for key in keys if key in release}
                    for release in data
                    if isinstance(release, dict)
                ]
                data = {
                    "scope": {
                        "max_records": RELEASE_PAGE_SIZE,
                        "history_complete": False,
                        "note": "Only one page of public release records is included.",
                    },
                    "releases": data,
                }
            content = self._limit(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
            return FetchedSource(
                url=repo.url + ("/releases" if kind == "releases" else ""),
                title=str(source.get("title") or f"{repo.owner}/{repo.name} {kind}"),
                content=content,
                version=content_version(content),
                etag=response.headers.get("etag", ""),
                fetched_at=iso(self._clock.now()),
            )
        commit_response = await self._get(
            client,
            repo.api + "/commits/" + quote(repo.ref, safe=""),
            etag=old_etag,
            accept="application/vnd.github.sha",
        )
        if commit_response.status_code == 304:
            return self._unchanged(source, previous, commit_response.headers.get("etag", ""))
        sha = commit_response.text.strip()
        # Older compatible proxies may still return the JSON representation.
        if not _SHA.fullmatch(sha):
            commit = self._json(commit_response)
            sha = commit.get("sha", "") if isinstance(commit, dict) else ""
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise SourceError(
                "GitHub did not return a full commit identifier.", code="invalid_response"
            )
        if repo.path:
            endpoint = repo.api + "/contents/" + quote(repo.path, safe="/") + "?ref=" + sha
        else:
            endpoint = repo.api + "/readme?ref=" + sha
        response = await self._get(client, endpoint)
        data = self._json(response)
        if (
            not isinstance(data, dict)
            or data.get("type", "file") != "file"
            or data.get("encoding") != "base64"
        ):
            raise SourceError(
                "GitHub did not return a readable text file.", code="invalid_response"
            )
        try:
            encoded = "".join(str(data["content"]).split())
            decoded = base64.b64decode(encoded, validate=True)
            content = decoded.decode("utf-8-sig")
        except (KeyError, UnicodeDecodeError, ValueError, binascii.Error) as exc:
            raise SourceError("The GitHub file is not UTF-8 text.", code="invalid_content") from exc
        if "\x00" in content:
            raise SourceError("Binary files are not supported.", code="invalid_content")
        content = self._limit(content.strip())
        if not content:
            raise SourceError("The GitHub file is empty.", code="empty_source")
        path = repo.path or data.get("path", "")
        if (
            not isinstance(path, str)
            or not path
            or any(p in ("", ".", "..") for p in path.split("/"))
        ):
            raise SourceError("GitHub returned an invalid file path.", code="invalid_response")
        return FetchedSource(
            url=f"{repo.url}/blob/{sha}/{quote(path, safe='/')}",
            title=str(source.get("title") or f"{repo.owner}/{repo.name}: {path}"),
            content=content,
            version=sha,
            # This ETag validates the ref-to-commit resolution on the next check.
            etag=commit_response.headers.get("etag", ""),
            fetched_at=iso(self._clock.now()),
        )

    async def _document(
        self, client: httpx.AsyncClient, source: dict[str, Any], previous: dict[str, Any] | None
    ) -> FetchedSource:
        etag = str(previous.get("etag", "")) if previous else ""
        response = await self._get(client, str(source["url"]), etag=etag)
        if response.status_code == 304:
            return self._unchanged(source, previous, response.headers.get("etag", ""))
        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        if content_type and not (
            content_type.startswith("text/")
            or content_type in ("application/json", "application/xhtml+xml")
        ):
            raise SourceError(
                "Only text, Markdown, HTML and JSON sources are supported.",
                code="unsupported_media",
            )
        try:
            body = response.content.decode(response.encoding or "utf-8-sig").lstrip("\ufeff")
        except (UnicodeDecodeError, LookupError) as exc:
            raise SourceError("The source is not readable text.", code="invalid_content") from exc
        if "\x00" in body:
            raise SourceError("Binary files are not supported.", code="invalid_content")
        title = ""
        if content_type in (
            "text/html",
            "application/xhtml+xml",
        ) or body.lstrip().lower().startswith(("<!doctype html", "<html")):
            title, content = await asyncio.to_thread(_document_text, body)
        elif content_type == "application/json":
            content = json.dumps(self._json(response), ensure_ascii=False, sort_keys=True, indent=2)
        else:
            content = body.strip()
        content = self._limit(content)
        if not content:
            raise SourceError("The source has no readable content.", code="empty_source")
        version = content_version(content)
        return FetchedSource(
            url=str(response.url),
            title=str(source.get("title") or title),
            content=content,
            version=version,
            etag=response.headers.get("etag", ""),
            fetched_at=iso(self._clock.now()),
            not_modified=bool(previous and previous.get("version") == version),
        )
