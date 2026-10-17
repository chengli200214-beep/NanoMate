"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { parsePairingShortcut, localUrlAllowed, externalUrlAllowed, trustedWindowSender, runtimeCommand, tryAttach } = require("../src/runtime.cjs");

test("private pairing must be a loopback assistant link with a fragment token", () => {
  const pair = parsePairingShortcut("[InternetShortcut]\nURL=http://127.0.0.1:12345/?tab=chat#token=local-example\n");
  assert.deepEqual(pair, { origin: "http://127.0.0.1:12345", token: "local-example" });
  for (const link of ["https://example.org/#token=local-example", "http://localhost:12345/#token=local-example", "http://127.0.0.1:12345/?token=local-example", "http://127.0.0.1:12345/api/files#token=local-example", "http://name:pass@127.0.0.1:12345/#token=local-example", "file:///tmp/app#token=local-example"]) assert.throws(() => parsePairingShortcut(`URL=${link}`));
});
test("navigation and external handoff accept only their intended schemes and origins", () => {
  assert.equal(localUrlAllowed("http://127.0.0.1:12345/?ui=desktop", "http://127.0.0.1:12345"), true);
  assert.equal(localUrlAllowed("http://127.0.0.1:12346/", "http://127.0.0.1:12345"), false);
  for (const raw of ["file:///C:/document.html", "javascript:alert(1)", "data:text/html,test", "http://user:pass@example.org/"]) assert.equal(externalUrlAllowed(raw), false);
  assert.equal(externalUrlAllowed("https://example.org/article"), true);
});
test("an artifact iframe, another webContents or local content cannot call window IPC", () => {
  const mainFrame = { url: "http://127.0.0.1:12345/?ui=desktop" };
  const contents = { mainFrame };
  const window = { webContents: contents };
  assert.equal(trustedWindowSender({ sender: contents, senderFrame: mainFrame }, window, "http://127.0.0.1:12345"), true);
  assert.equal(trustedWindowSender({ sender: contents, senderFrame: { url: mainFrame.url } }, window, "http://127.0.0.1:12345"), false);
  assert.equal(trustedWindowSender({ sender: {}, senderFrame: mainFrame }, window, "http://127.0.0.1:12345"), false);
  mainFrame.url = "http://127.0.0.1:12345/api/files/html";
  assert.equal(trustedWindowSender({ sender: contents, senderFrame: mainFrame }, window, "http://127.0.0.1:12345"), false);
});
test("an unrelated or missing profile is never adopted", async () => {
  assert.equal(await tryAttach(require("node:path").join(require("node:os").tmpdir(), `missing-assistant-${Date.now()}`)), null);
});
test("a packaged app never falls back to a machine Python installation", () => {
  assert.throws(() => runtimeCommand({ packaged: true, resourcesPath: "/absent-build-resources", root: "/", python: "python" }), /bundled assistant runtime is missing/);
});
