import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthError } from "./api";
import { isActiveRun, repositoryUrl, researchApi, runControls, type ResearchRun } from "./research-api";

beforeEach(() => {
  vi.stubGlobal("window", { location: { href: "https://example.org/#research" }, history: { replaceState: () => undefined } });
  vi.stubGlobal("localStorage", { getItem: () => "test-token", setItem: () => undefined, removeItem: () => undefined });
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("research requests", () => {
  it("uses the shared Bearer authentication and encodes project IDs", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await researchApi.evidence("project/a")).toEqual([]);
    expect(fetcher).toHaveBeenCalledWith("/api/research/projects/project%2Fa/evidence", expect.objectContaining({ headers: { Authorization: "Bearer test-token" } }));
  });

  it("keeps the same idempotency key in the run request", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    await researchApi.start("project", "research", "stable-request");
    const init = fetcher.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({ kind: "research", request_key: "stable-request" });
  });

  it("exports raw Markdown without exposing the token in a URL", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("# Report\n\nEvidence.", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await researchApi.exportMarkdown("report/a")).toBe("# Report\n\nEvidence.");
    expect(fetcher).toHaveBeenCalledWith("/api/research/reports/report%2Fa/export", { headers: { Authorization: "Bearer test-token" } });
  });

  it("preserves export errors and identifies expired authentication", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('{"detail":"report is not ready"}', { status: 409 })).mockResolvedValueOnce(new Response("", { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(researchApi.exportMarkdown("r")).rejects.toThrow("report is not ready");
    await expect(researchApi.exportMarkdown("r")).rejects.toBeInstanceOf(AuthError);
  });
});

describe("research input and control boundaries", () => {
  it("accepts only repository roots and canonicalizes .git links", () => {
    expect(repositoryUrl(" https://github.com/nano-muse/nanoMuse.git/ ")).toBe("https://github.com/nano-muse/nanoMuse");
    for (const value of ["https://github.com/a/b/issues", "http://github.com/a/b", "https://elsewhere.org/a/b", "https://user:secret@github.com/a/b", "https://github.com/a/b?token=secret", "https://github.com/a/b#readme", "not a URL"]) expect(repositoryUrl(value), value).toBeNull();
  });

  it("stops polling paused and attention states, with only valid controls", () => {
    expect(isActiveRun({ status: "running" } as ResearchRun)).toBe(true);
    for (const status of ["paused", "needs_attention", "succeeded", "failed", "cancelled"]) expect(isActiveRun({ status } as ResearchRun)).toBe(false);
    expect(runControls("paused")).toEqual(["resume", "cancel"]);
    expect(runControls("needs_attention")).toEqual(["resume", "retry", "cancel"]);
    expect(runControls("succeeded")).toEqual([]);
  });
});
