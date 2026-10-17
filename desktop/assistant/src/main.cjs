"use strict";

const { app, BrowserWindow, Menu, Tray, nativeImage, ipcMain, shell, dialog, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { startRuntime, localUrlAllowed, externalUrlAllowed, trustedWindowSender, readJson } = require("./runtime.cjs");

app.setName("NanoMate");
app.setAppUserModelId("io.github.nanomuse.assistant");
const value = (name) => process.argv.find((arg) => arg.startsWith(`--${name}=`))?.slice(name.length + 3);
// Retain the original profile, Vault, model limits and Electron preferences after rebranding.
app.setPath("userData", value("user-data") ? path.resolve(value("user-data")) : path.join(app.getPath("appData"), "nanoMuse Assistant"));
const root = path.resolve(__dirname, "../../..");
const userData = app.getPath("userData");
const prefsFile = path.join(userData, "desktop-preferences.json");
let preferences = { mode: "offline", port: 0, profile: null };
try {
  const saved = JSON.parse(fs.readFileSync(prefsFile, "utf8"));
  if (saved.mode === "live") preferences.mode = "live";
  if (Number.isInteger(saved.port) && saved.port >= 1024 && saved.port <= 65535) preferences.port = saved.port;
  if (typeof saved.profile === "string" && path.isAbsolute(saved.profile)) preferences.profile = saved.profile;
} catch { /* New local profile. */ }
if (process.argv.includes("--offline")) preferences.mode = "offline";
if (process.argv.includes("--live")) preferences.mode = "live";
const dataDir = path.resolve(value("profile") || preferences.profile || path.join(userData, "assistant"));
let win = null;
let tray = null;
let runtime = null;
let quitting = false;
let closeNoticeShown = false;
let ready = false;
let booting = false;
const zh = () => app.getLocale().toLowerCase().startsWith("zh");
const text = (en, cn) => zh() ? cn : en;
function startupMessage(error) {
  const messages = {
    "The bundled assistant runtime is missing. Reinstall this desktop build.": "内置运行时不完整，请重新安装桌面端。",
    "Prepare the assistant Python environment before starting the source build.": "源码启动前，请先按文档准备个人助手的 Python 环境。",
    "The local assistant could not start. Open desktop logs for details. Existing services were left running.": "本地助手未能启动，请查看桌面日志。已有服务会继续运行。",
    "The local assistant took too long to start. Open desktop logs and try again.": "本地助手启动超时，请查看桌面日志后重试。",
  };
  if (Object.hasOwn(messages, error.message)) return text(error.message, messages[error.message]);
  // Electron load errors can contain a pairing fragment. Never surface the raw URL.
  return text("The workspace could not open. Review the desktop logs and try again.", "工作空间未能打开，请查看桌面日志后重试。");
}
const bootPath = path.resolve(__dirname, "../resources/boot.html");
const iconPath = app.isPackaged ? path.join(process.resourcesPath, "app-icon.png") : path.join(root, "harness/desktop/resources/app-icon.png");
const smokePath = value("smoke-output") ? path.resolve(value("smoke-output")) : null;

if (!app.requestSingleInstanceLock({ profile: dataDir })) app.quit();
else {
  app.on("second-instance", () => show());
  app.on("activate", () => show());
  app.whenReady().then(launch).catch(() => app.quit());
}

function show(tab) {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  win.show(); win.focus();
  if (tab && runtime && ready) {
    const target = new URL(win.webContents.getURL());
    target.hash = tab;
    void win.loadURL(target.toString());
  } else if (ready) win.webContents.send("assistant:quick-chat");
}

function buildMenu() {
  const template = [
    { label: text("Workspace", "工作空间"), submenu: [
      { label: text("Show assistant", "打开个人助手"), click: () => show(), accelerator: "CmdOrCtrl+Shift+Space" },
      { label: text("Connections", "连接与模型"), click: () => show("connections") },
      { label: text("Task ledger", "任务账本"), click: () => show("tasks") },
      { label: text("Research", "调研模块"), click: () => show("research") },
      { type: "separator" },
      { label: text("Open workspace folder", "打开工作空间文件夹"), click: () => shell.openPath(path.join(dataDir, "workspace")) },
      { label: text("Open desktop logs", "打开桌面端日志"), click: () => shell.openPath(path.join(userData, "desktop-logs")) },
      { label: text("Open local limit configuration", "打开本地额度配置"), click: () => shell.openPath(path.join(userData, "assistant.toml")) },
      { type: "separator" },
      { label: text("Exit", "退出"), click: () => void exit(), accelerator: "Alt+F4" },
    ] },
    { label: text("Edit", "编辑"), submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
    { label: text("View", "视图"), submenu: [{ role: "reload" }, { type: "separator" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }] },
    { label: text("Runtime", "运行模式"), submenu: [
      { label: text("Offline scripted responses", "离线脚本响应"), type: "radio", checked: preferences.mode === "offline", click: () => void changeMode("offline") },
      { label: text("Use configured model", "使用已配置的模型"), type: "radio", checked: preferences.mode === "live", click: () => void changeMode("live") },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  tray?.setContextMenu(Menu.buildFromTemplate([
    { label: "NanoMate", enabled: false },
    { label: text("Open assistant", "打开个人助手"), click: () => show() },
    { label: text("Task ledger", "任务账本"), click: () => show("tasks") },
    { label: text("Connections", "连接与模型"), click: () => show("connections") },
    { type: "separator" }, { label: text("Exit", "退出"), click: () => void exit() },
  ]));
}

async function launch() {
  win = new BrowserWindow({
    width: 1320, height: 900, minWidth: 720, minHeight: 620, show: false, frame: false, autoHideMenuBar: true,
    title: "NanoMate", backgroundColor: "#faf9f6", icon: iconPath,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true, allowRunningInsecureContent: false, spellcheck: false },
  });
  win.once("ready-to-show", () => win.show());
  win.on("close", (event) => {
    if (quitting || smokePath) return;
    event.preventDefault();
    if (!tray) { void exit(); return; }
    win.hide();
    if (!closeNoticeShown) { closeNoticeShown = true; tray.displayBalloon({ title: "NanoMate", content: text("The assistant is in the system tray. Right-click its icon to exit.", "个人助手已收起到系统托盘。右键托盘图标可以退出。") }); }
  });
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  win.webContents.on("will-attach-webview", (event) => event.preventDefault());
  win.webContents.setWindowOpenHandler(({ url }) => { if (externalUrlAllowed(url)) void shell.openExternal(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (event, url) => {
    if (runtime && localUrlAllowed(url, runtime.origin) && new URL(url).pathname === "/") return;
    event.preventDefault();
    if (externalUrlAllowed(url) && (!runtime || !localUrlAllowed(url, runtime.origin))) void shell.openExternal(url);
  });
  session.defaultSession.on("will-download", (event, item) => {
    if (!runtime || !localUrlAllowed(item.getURL(), runtime.origin)) { event.preventDefault(); return; }
    item.setSaveDialogOptions({ title: text("Save assistant output", "保存助手产物"), defaultPath: path.join(app.getPath("downloads"), path.basename(item.getFilename()).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")) });
  });
  ipcMain.on("assistant:window", (event, action) => {
    const trustedBoot = event.sender === win?.webContents && event.senderFrame === win.webContents.mainFrame && event.senderFrame?.url === require("node:url").pathToFileURL(bootPath).href;
    if (!trustedBoot && !trustedWindowSender(event, win, runtime?.origin)) return;
    if (action === "minimize") win.minimize();
    if (action === "maximize") win.isMaximized() ? win.unmaximize() : win.maximize();
    if (action === "close") win.close();
  });
  const image = nativeImage.createFromPath(iconPath).resize({ width: 20, height: 20 });
  tray = new Tray(image);
  tray.setToolTip("NanoMate");
  tray.on("double-click", () => show());
  buildMenu();
  await win.loadFile(bootPath);
  await boot();
}

async function boot() {
  if (booting || quitting) return;
  booting = true; ready = false;
  try {
    runtime = await startRuntime({ packaged: app.isPackaged, resourcesPath: process.resourcesPath, root, python: value("python"), userData, dataDir, mode: preferences.mode, preferredPort: preferences.port, attach: !process.argv.includes("--no-attach") });
    const url = new URL("/", runtime.origin);
    url.searchParams.set("ui", "desktop"); url.searchParams.set("tab", "chat");
    url.hash = new URLSearchParams({ token: runtime.token }).toString();
    await win.loadURL(url.toString());
    win.show();
    ready = true;
    preferences.mode = runtime.mode;
    preferences.port = Number(new URL(runtime.origin).port);
    preferences.profile = dataDir;
    fs.writeFileSync(prefsFile, JSON.stringify(preferences));
    buildMenu();
    runtime.process?.once("exit", () => {
      if (!quitting && ready) { ready = false; void dialog.showMessageBox(win, { type: "error", message: text("The local assistant stopped.", "本地助手服务已停止。"), detail: text("Your saved data is retained. Restart the desktop app to reconnect.", "已保存的数据会保留。请重新打开桌面端连接。") }); }
    });
    if (smokePath) {
      await new Promise((resolve) => setTimeout(resolve, 3500));
      const inventory = await readJson(runtime.origin, "/api/assistant/capabilities", runtime.token);
      fs.mkdirSync(smokePath, { recursive: true });
      fs.writeFileSync(path.join(smokePath, "desktop.png"), (await win.webContents.capturePage()).toPNG());
      fs.writeFileSync(path.join(smokePath, "smoke.json"), JSON.stringify({ packaged: app.isPackaged, mode: inventory.mode, owned: runtime.owned, window: !win.isDestroyed(), title: win.getTitle(), profileRemembered: JSON.parse(fs.readFileSync(prefsFile, "utf8")).profile === dataDir, defaultProfile: dataDir === path.join(userData, "assistant"), nodeIntegration: win.webContents.getLastWebPreferences().nodeIntegration, sandbox: win.webContents.getLastWebPreferences().sandbox, contextIsolation: win.webContents.getLastWebPreferences().contextIsolation, webContentsLoaded: !win.webContents.isLoading(), userAgent: win.webContents.getUserAgent() }, null, 2));
      await exit(true);
    }
  } catch (error) {
    if (smokePath) { fs.mkdirSync(smokePath, { recursive: true }); fs.writeFileSync(path.join(smokePath, "failure.txt"), startupMessage(error)); await exit(true); process.exitCode = 1; }
    else {
      const answer = await dialog.showMessageBox(win, { type: "error", title: "NanoMate", message: text("Could not start your workspace.", "工作空间未能启动。"), detail: startupMessage(error), buttons: [text("Retry", "重试"), text("Open logs", "打开日志"), text("Exit", "退出")], defaultId: 0 });
      booting = false;
      if (answer.response === 0) return boot();
      if (answer.response === 1) await shell.openPath(path.join(userData, "desktop-logs"));
      await exit(true);
    }
  } finally { booting = false; }
}

async function changeMode(mode) {
  if (!runtime?.owned || booting) {
    await dialog.showMessageBox(win, { message: text("This window is connected to an existing assistant.", "此窗口连接的是已经运行的助手。"), detail: text("Change its startup mode from the launcher that owns it. The desktop will not stop that service.", "请通过启动该服务的入口更改模式，桌面端不会停止它。") });
    buildMenu(); return;
  }
  const answer = await dialog.showMessageBox(win, { type: "question", message: text("Restart the local assistant?", "重新启动本地助手？"), detail: mode === "live" ? text("The saved model will be used. Local request and spending limits still apply. Active work will be interrupted.", "将使用你保存的模型，原有请求和费用上限继续生效。进行中的任务会被中断。") : text("Use labelled scripted responses. Active work will be interrupted.", "切换为明确标注的脚本响应。进行中的任务会被中断。"), buttons: [text("Cancel", "取消"), text("Restart", "重新启动")], defaultId: 0, cancelId: 0 });
  if (answer.response !== 1) { buildMenu(); return; }
  ready = false;
  await runtime.stop(); runtime = null;
  preferences.mode = mode;
  fs.writeFileSync(prefsFile, JSON.stringify(preferences));
  await win.loadFile(bootPath);
  await boot();
}

async function exit(force = false) {
  if (quitting) return;
  if (!force && runtime?.owned) {
    const answer = await dialog.showMessageBox(win, { type: "question", message: text("Exit NanoMate?", "退出 NanoMate？"), detail: text("The local service started by this desktop will stop. Saved data remains on this computer. Closing the window keeps it running in the tray.", "桌面端启动的本地服务会停止，已保存的数据仍保留在本机。仅关闭窗口会继续在托盘运行。"), buttons: [text("Keep running", "继续运行"), text("Exit", "退出")], defaultId: 0, cancelId: 0 });
    if (answer.response !== 1) return;
  }
  quitting = true; ready = false;
  await runtime?.stop();
  tray?.destroy(); tray = null;
  app.quit();
}
app.on("before-quit", (event) => { if (!quitting) { event.preventDefault(); void exit(true); } });
app.on("window-all-closed", () => { if (!tray) void exit(true); });
