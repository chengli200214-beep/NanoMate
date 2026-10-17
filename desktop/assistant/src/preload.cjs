"use strict";
const { contextBridge, ipcRenderer } = require("electron");

// Downloaded HTML and embedded artifacts never receive a privileged bridge.
const mainSurface = process.isMainFrame && ((location.protocol === "http:" && location.hostname === "127.0.0.1" && location.pathname === "/") || (location.protocol === "file:" && location.pathname.endsWith("/boot.html")));
if (mainSurface) contextBridge.exposeInMainWorld("nanomuseDesktop", Object.freeze({
  platform: process.platform,
  version: "1.0.5",
  surface: "assistant",
  windowAction: (action) => { if (["minimize", "maximize", "close"].includes(action)) ipcRenderer.send("assistant:window", action); },
  onQuickChat: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("assistant:quick-chat", listener);
    return () => ipcRenderer.removeListener("assistant:quick-chat", listener);
  },
}));
