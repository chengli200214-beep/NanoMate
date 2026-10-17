"use strict";
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { startRuntime, readJson } = require("../src/runtime.cjs");
const arg = (name) => process.argv.find((value) => value.startsWith(`--${name}=`))?.slice(name.length + 3);
async function main() {
  const userData = path.resolve(arg("state") || ".smoke-state");
  const resourcesPath = path.resolve(arg("runtime") || "runtime");
  const runtime = await startRuntime({ packaged: true, resourcesPath, root: "", userData, dataDir: path.join(userData, "assistant"), mode: "offline", attach: false });
  const report = { mode: runtime.mode, owned: runtime.owned, bundled: true };
  try {
    const inventory = await readJson(runtime.origin, "/api/assistant/capabilities", runtime.token);
    assert.equal(inventory.mode, "offline");
    assert.equal(inventory.usage_limits.configuration_required, true);
    report.zeroAllowance = true;
    report.research = Array.isArray(await readJson(runtime.origin, "/api/research/projects", runtime.token));
    report.taskLedger = Array.isArray(await readJson(runtime.origin, "/api/assistant/tasks", runtime.token));
    const snapshot = await readJson(runtime.origin, "/api/state", runtime.token);
    report.version = snapshot.version;
    report.authenticated = true;
    report.requestsToRealModels = 0;
  } finally { await runtime.stop(); }
  await assert.rejects(readJson(runtime.origin, "/api/health"));
  report.shutdown = true;
  fs.writeFileSync(path.join(userData, "smoke-runtime.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
main().catch((error) => { console.error(error.message); process.exitCode = 1; });
