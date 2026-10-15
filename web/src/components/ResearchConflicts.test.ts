import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { setLocaleSetting } from "../i18n";
import type { ResearchConflict, ResearchEvidence } from "../research-api";
import { ResearchConflicts } from "./ResearchConflicts";

const evidence: ResearchEvidence[] = ["one", "two"].map((name) => ({
  id: `evidence-${name}`, project_id: "project", candidate: "https://github.com/example/runtime",
  dimension: "platform", fact: `Claim ${name}`, quote: `Source quote ${name}`, snapshot_id: `snapshot-${name}`,
  locator: "README", verification: "documentation", status: "active", source_url: `https://github.com/example/runtime/tree/${name}`,
  version: name, fetched_at: "2026-01-01T00:00:00Z",
}));
const open: ResearchConflict = {
  id: "conflict-open", project_id: "project", candidate: evidence[0].candidate, dimension: "platform",
  evidence_refs: evidence.map((item) => item.id), reason: "The documentation and source disagree.",
  version_scope: "README one and source two", status: "open", created_at: "2026-01-01T00:00:00Z",
  spec_id: "spec", run_id: "run",
};
let screen: ReactTestRenderer;
beforeEach(() => setLocaleSetting("en"));
afterEach(() => { if (screen) act(() => screen.unmount()); });

describe("evidence conflicts", () => {
  it("shows both source-backed claims without changing their active status", () => {
    act(() => { screen = create(createElement(ResearchConflicts, { conflicts: [open], evidence })); });
    const rendered = JSON.stringify(screen.toJSON());
    for (const value of [open.reason, open.version_scope, "evidence-one", "evidence-two", "Claim one", "Claim two", "Source quote one", "Source quote two"]) expect(rendered).toContain(value);
    expect(rendered).toContain("Decisions affected by an open conflict stay unknown.");
    expect(screen.root.findAllByType("a").map((node) => node.props.href)).toEqual(evidence.map((item) => item.source_url));
    expect(evidence.map((item) => item.status)).toEqual(["active", "active"]);
  });

  it("keeps superseded conflicts in a collapsed history", () => {
    const previous: ResearchConflict = { ...open, id: "conflict-old", status: "superseded" };
    act(() => { screen = create(createElement(ResearchConflicts, { conflicts: [previous], evidence })); });
    const history = screen.root.findByType("details");
    expect(history.props.open).not.toBe(true);
    const rendered = JSON.stringify(screen.toJSON());
    expect(rendered).toContain("Previous conflicts (1)");
    expect(rendered).toContain("Superseded conflict");
    expect(rendered).toContain("No open evidence conflicts.");
  });

  it("names missing references and never makes an executable source URL clickable", () => {
    const unsafe = { ...evidence[0], source_url: "javascript:alert(1)" };
    act(() => { screen = create(createElement(ResearchConflicts, { conflicts: [open], evidence: [unsafe] })); });
    expect(screen.root.findAllByType("a")).toHaveLength(0);
    expect(JSON.stringify(screen.toJSON())).toContain("This evidence is not available in the current ledger.");
    expect(JSON.stringify(screen.toJSON())).toContain("evidence-two");
  });
});
