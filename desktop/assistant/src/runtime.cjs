"use strict";

const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const net = require("node:net");
const { spawn } = require("node:child_process");

/** Pairing tokens never appear in process arguments, logs, query strings or error text. */
function parsePairingShortcut(contents) {
  const raw = contents.split(/\r?\n/).find((line) => line.startsWith("URL="))?.slice(4);
  if (!raw) throw new Error("No pairing address.");
  const url = new URL(raw);
  const token = new URLSearchParams(url.hash.slice(1)).get("token");
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port ||
      url.username || url.password || url.pathname !== "/" || !token || /[\r\n]/.test(token)) {
    throw new Error("Invalid local pairing address.");
  }
  return { origin: url.origin, token };
}

function localUrlAllowed(raw, origin) {
  try { const url = new URL(raw); return url.origin === origin && !url.username && !url.password; }
  catch { return false; }
}
function externalUrlAllowed(raw) {
  try { const url = new URL(raw); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password; }
  catch { return false; }
}
function trustedWindowSender(event, window, origin) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return false;
  try { const url = new URL(event.senderFrame.url); return url.origin === origin && url.pathname === "/"; }
  catch { return false; }
}

/** node:http intentionally bypasses environment proxies for loopback authentication. */
function readJson(origin, route, token, timeoutMs = 1600) {
  return new Promise((resolve, reject) => {
    const url = new URL(route, origin);
    if (url.hostname !== "127.0.0.1" || url.protocol !== "http:") return reject(new Error("Local runtime only."));
    const req = http.get(url, { headers: token ? { Authorization: `Bearer ${token}` } : {}, timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; if (body.length > 4 * 1024 * 1024) req.destroy(new Error("Runtime response too large.")); });
      res.on("end", () => {
        if (res.statusCode !== 200) return reject(new Error("Runtime did not accept the local connection."));
        try { resolve(JSON.parse(body)); } catch { reject(new Error("Invalid runtime response.")); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("Local runtime timed out.")));
    req.on("error", () => reject(new Error("Local runtime unavailable.")));
  });
}

async function tryAttach(dataDir) {
  try {
    const marker = JSON.parse(fs.readFileSync(path.join(dataDir, "assistant-profile.json"), "utf8"));
    if (marker.profile !== "assistant" || marker.version !== 1) return null;
    const pairing = parsePairingShortcut(fs.readFileSync(path.join(dataDir, "open-assistant.url"), "utf8"));
    const inventory = await readJson(pairing.origin, "/api/assistant/capabilities", pairing.token);
    if (!["offline", "live"].includes(inventory.mode)) return null;
    return { ...pairing, owned: false, mode: inventory.mode, process: null, stop: async () => {} };
  } catch { return null; }
}

function freePort(preferred = 0) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", (error) => {
      if (preferred && error.code === "EADDRINUSE") freePort().then(resolve, reject);
      else reject(error);
    });
    server.listen(preferred, "127.0.0.1", () => {
      const port = server.address().port;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

function runtimeCommand({ packaged, resourcesPath, root, python }) {
  if (packaged) {
    const file = path.join(resourcesPath, "assistant-runtime", process.platform === "win32" ? "assistant-runtime.exe" : "assistant-runtime");
    if (!fs.existsSync(file)) throw new Error("The bundled assistant runtime is missing. Reinstall this desktop build.");
    return { executable: file, args: [] };
  }
  const interpreter = python || [".local/assistant-lock-verify/Scripts/python.exe", ".venv/Scripts/python.exe", ".venv/bin/python"].map((p) => path.join(root, p)).find(fs.existsSync);
  if (!interpreter) throw new Error("Prepare the assistant Python environment before starting the source build.");
  return { executable: interpreter, args: [path.join(root, "scripts", "assistant_dev.py")] };
}

async function startRuntime(options) {
  const attached = options.attach && await tryAttach(options.dataDir);
  if (attached) return attached;
  const port = await freePort(options.preferredPort || 0);
  const command = runtimeCommand(options);
  // The profile rejects foreign/nonempty directories. Do not place desktop logs inside it.
  const logsDir = path.join(options.userData, "desktop-logs");
  fs.mkdirSync(logsDir, { recursive: true });
  const configFile = path.join(options.userData, "assistant.toml");
  if (!fs.existsSync(configFile)) fs.writeFileSync(configFile,
    "# Local desktop limits. Restart the assistant after editing.\n" +
    "# 本地调用额度，修改后请重新启动助手。不要在这里填写密钥。\n" +
    "[assistant_usage]\nenabled = true\nprice_policy = \"\"\ndaily_cny = 0\ntask_cny = 0\n");
  const log = fs.openSync(path.join(logsDir, "runtime.log"), "a");
  const args = [...command.args, "--data-dir", options.dataDir, "--port", String(port), "--config", configFile, "--desktop-control"];
  if (options.mode === "offline") args.push("--offline");
  const env = { ...process.env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1", NO_PROXY: "127.0.0.1,localhost,::1" };
  // Credentials enter through the local Vault UI, never an inherited shell environment.
  for (const key of Object.keys(env)) if (/^NANOMUSE_|(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|BASE_URL)$/.test(key) || ["NODE_OPTIONS", "ELECTRON_RUN_AS_NODE"].includes(key)) delete env[key];
  if (!options.packaged) env.PYTHONPATH = options.root;
  const browsers = options.packaged ? path.join(options.resourcesPath, "browsers") : path.join(options.root, ".local", "p4-browsers");
  if (fs.existsSync(browsers)) env.PLAYWRIGHT_BROWSERS_PATH = browsers;
  const child = spawn(command.executable, args, { cwd: options.packaged ? options.userData : options.root, env, windowsHide: true, stdio: ["pipe", log, log], shell: false });
  fs.closeSync(log);
  let failure = false;
  child.once("error", () => { failure = true; });
  const alive = () => !failure && child.exitCode === null && child.signalCode === null;
  let stopped = false;
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    if (!alive()) return;
    const ended = new Promise((resolve) => child.once("exit", resolve));
    child.stdin.on("error", () => {});
    child.stdin.end("shutdown\n");
    let timer;
    await Promise.race([ended, new Promise((resolve) => { timer = setTimeout(resolve, 15000); })]);
    clearTimeout(timer);
    if (alive()) { child.kill(); await Promise.race([ended, new Promise((resolve) => setTimeout(resolve, 3000))]); }
  };
  try {
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      if (!alive()) throw new Error("The local assistant could not start. Open desktop logs for details. Existing services were left running.");
      try {
        const pairing = parsePairingShortcut(fs.readFileSync(path.join(options.dataDir, "open-assistant.url"), "utf8"));
        if (pairing.origin !== `http://127.0.0.1:${port}`) throw new Error("Waiting for this process.");
        const inventory = await readJson(pairing.origin, "/api/assistant/capabilities", pairing.token);
        if (alive() && ["offline", "live"].includes(inventory.mode)) return { ...pairing, owned: true, process: child, mode: inventory.mode, stop };
      } catch { /* The authenticated service is still starting. */ }
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    throw new Error("The local assistant took too long to start. Open desktop logs and try again.");
  } catch (error) { await stop(); throw error; }
}

module.exports = { parsePairingShortcut, localUrlAllowed, externalUrlAllowed, trustedWindowSender, readJson, tryAttach, runtimeCommand, startRuntime };
