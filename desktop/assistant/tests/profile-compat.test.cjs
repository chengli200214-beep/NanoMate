"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

function bootstrap({ override = false, explicitProfile = false } = {}) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "nanomate-profile-compat-"));
  const appData = path.join(base, "Roaming");
  const original = path.join(appData, "nanoMuse Assistant");
  const userData = override ? path.join(base, "chosen-desktop-data") : original;
  const savedProfile = path.join(base, "existing-assistant");
  const chosenProfile = path.join(base, "chosen-assistant");
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(path.join(userData, "desktop-preferences.json"), JSON.stringify({ mode: "live", port: 15432, profile: savedProfile }));
  const paths = { appData };
  let name = "NanoMate";
  let lockedProfile;
  const app = {
    setName(value) { name = value; },
    setAppUserModelId() {},
    setPath(key, value) { paths[key] = value; },
    getPath(key) { return paths[key] || path.join(appData, name); },
    requestSingleInstanceLock(value) { lockedProfile = value.profile; return false; },
    quit() {},
    on() {},
  };
  const argv = ["NanoMate.exe"];
  if (override) argv.push(`--user-data=${userData}`);
  if (explicitProfile) argv.push(`--profile=${chosenProfile}`);
  const filename = path.resolve(__dirname, "../src/main.cjs");
  const context = vm.createContext({
    __dirname: path.dirname(filename),
    process: { argv },
    require(id) {
      if (id === "electron") return { app };
      if (id === "./runtime.cjs") return {};
      return require(id);
    },
  });
  vm.runInContext(fs.readFileSync(filename, "utf8"), context, { filename });
  const result = JSON.parse(JSON.stringify(vm.runInContext("({ userData, dataDir, preferences })", context)));
  return { ...result, original, userDataExpected: userData, savedProfile, chosenProfile, lockedProfile, name };
}

test("NanoMate retains the previous desktop directory and saved live profile before taking the instance lock", () => {
  const state = bootstrap();
  assert.equal(state.name, "NanoMate");
  assert.equal(state.userData, state.original);
  assert.equal(state.dataDir, state.savedProfile);
  assert.equal(state.lockedProfile, state.savedProfile);
  assert.equal(state.preferences.mode, "live");
  assert.equal(state.preferences.port, 15432);
});

test("explicit desktop and assistant directories still override saved preferences", () => {
  const state = bootstrap({ override: true, explicitProfile: true });
  assert.equal(state.userData, state.userDataExpected);
  assert.equal(state.dataDir, state.chosenProfile);
  assert.equal(state.lockedProfile, state.chosenProfile);
  assert.equal(state.preferences.mode, "live");
});
