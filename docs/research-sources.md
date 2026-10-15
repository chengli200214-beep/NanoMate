# Research sources

The research source adapters read registered public URLs. They do not clone a
repository, run its code, follow links inside its documents, or add sources suggested
by a model. The research tool boundary and Sentinel decide which registered source
can be read. The adapters implement `SourceFetcher` in `nanomuse/research/contracts.py`.

## Live reads

`HttpSourceFetcher` accepts these source kinds:

| Kind | Input | Saved content and version |
|---|---|---|
| `readme` | Public GitHub repository URL | README at the resolved full commit |
| `file` | Exact GitHub `blob` or `raw` file URL | UTF-8 file at the resolved full commit |
| `metadata` | Public GitHub repository URL | Selected metadata JSON, with a content hash |
| `releases` | Public GitHub repository URL | Up to 20 published release records, with a content hash |
| `document` | An explicitly added documentation URL | Readable Markdown, plain text or JSON, with a content hash |
| `manual` | Pasted Markdown or text, with an optional source URL | Original text, with a content hash |

For a branch name containing a slash, encode the slash as `%2F` in a file URL, or
use a full commit. A file source requires an exact file URL. Adding a repository
does not give the adapter permission to discover or download arbitrary files.

GitHub file reads first resolve the ref, then request the file with that commit as
`ref`. The returned URL contains the full commit. The saved ETag belongs to the
ref resolution, so a later `304` can reuse the saved file without downloading it.
Metadata, releases and documentation use their endpoint's ETag. A `304` requires
a previous snapshot. A failed request raises an error and does not relabel old
content as freshly verified.

HTML cleaning removes navigation, headers, footers, scripts, forms, sidebars,
hidden elements and comments. Main content keeps its version and dependency text.
Versions for documents and manual text are SHA-256 hashes of the saved content.
Documents with identical cleaned content are marked unchanged even if page chrome
changed. GitHub metadata such as stars remains a maintenance signal, not a quality
verdict.

The defaults are a 30-second deadline per request including DNS, redirects and body
reading, five redirects, 2 MiB per response, and 200,000 saved characters. Exceeding
a limit rejects the source instead of silently truncating evidence. The adapter
asks for an uncompressed response and rejects an unexpected compressed response.

## Network boundary

Only HTTP and HTTPS URLs without credentials are accepted. Local names, private,
reserved, loopback, link-local and IP transition addresses are refused. Every DNS
answer must be public. Each request, including each redirect, is checked again.
The connection uses the checked IP with the original Host and TLS server name, so
a second DNS lookup cannot change the destination. Environment proxy settings are
disabled. HTTPS redirects cannot downgrade to HTTP. Authorization and conditional
headers are removed on a redirect to another origin.

An optional `github_token` constructor argument is sent only to the HTTPS GitHub
API. If omitted, it reads `NANOMUSE_RESEARCH_GITHUB_TOKEN`; passing an empty string
disables token use. It is not put into returned content or error messages. Injecting a transport
is a trusted testing seam: the mock receives an IP URL, the original Host header,
and the `sni_hostname` extension. A resolver has the async signature
`resolver(host: str, port: int) -> Sequence[str]`.

`SourceError` exposes `code`, `retryable`, and optional `retry_after` seconds.
Authentication and permission failures require user action. Timeouts, DNS failures,
temporary HTTP errors and rate limits can be retried by the orchestrator. Rate
limits preserve `Retry-After` or GitHub's reset time when provided. Adapters never
sleep or retry on their own.

## Explicit fixture replay

`FixtureFetcher()` loads bundled README excerpts pinned to commits and captured
release-feed excerpts for nanoMuse and Open Dots. Each entry records its public
source, capture time, content version and selection. README provenance includes
the original file hash and the selected line ranges. These are attributed source
excerpts, not a live capability verification or permission to copy upstream code.

A mapping or a JSON file can provide other fixed snapshots. JSON must declare
`"fixture": true` and contain a `sources` mapping. Lookup keys can be source IDs,
`kind:url`, or exact URLs. Kind-prefixed keys distinguish a README from releases
on the same repository. Missing keys raise `fixture_missing`; live failure never
switches to fixtures automatically.

Fixture replay sends no HTTP or DNS by default. It still validates the input and
saved URLs against the syntax, local-name and literal-IP boundary, and can only
read registered entries. An injected resolver can additionally validate saved
hostname answers. It cannot turn a localhost HTTP server into a permitted source.
The application must label a fixture project and its scripted model results.

Run adapter tests with `python -m pytest -q tests/research/test_sources.py`.
