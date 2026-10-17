"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { startRuntime, tryAttach, readJson } = require("../src/runtime.cjs");

test("owned runtime shuts down, an attached window cannot stop it, and a locked profile is preserved", { skip: !process.env.ASSISTANT_DESKTOP_TEST_PYTHON, timeout: 90000 }, async () => {
  const root = path.resolve(__dirname, "../../..");
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "nanomuse-desktop-lifecycle-"));
  const dataDir = path.join(userData, "assistant");
  const options = { root, userData, dataDir, packaged: false, python: process.env.ASSISTANT_DESKTOP_TEST_PYTHON, mode: "offline", attach: true };
  const owner = await startRuntime(options);
  try {
    assert.equal(owner.owned, true);
    assert.equal(owner.mode, "offline");
    const attached = await tryAttach(dataDir);
    assert.equal(attached.owned, false);
    await attached.stop();
    assert.equal((await readJson(owner.origin, "/api/assistant/capabilities", owner.token)).mode, "offline");
    const marker = fs.readFileSync(path.join(dataDir, "assistant-profile.json"), "utf8");
    await assert.rejects(startRuntime({ ...options, attach: false }), /could not start/);
    assert.equal(fs.readFileSync(path.join(dataDir, "assistant-profile.json"), "utf8"), marker);
    assert.equal((await readJson(owner.origin, "/api/health")).ok, true);
  } finally { await owner.stop(); }
  await assert.rejects(readJson(owner.origin, "/api/health"));
  const reopened = await startRuntime(options);
  assert.equal(reopened.owned, true);
  await reopened.stop();
  // Deliberately retain the temporary profile for diagnosis; it contains no real keys.
});
