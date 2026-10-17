"use strict";
const zh = navigator.language.startsWith("zh");
document.documentElement.lang = zh ? "zh-CN" : "en";
document.getElementById("window-label").textContent = zh ? "nanoMuse · 个人助手" : "nanoMuse · Personal assistant";
document.getElementById("close").setAttribute("aria-label", zh ? "关闭" : "Close");
document.getElementById("title").textContent = zh ? "从日常小事，到认真思考。" : "A little help, every day.";
document.getElementById("detail").textContent = zh ? "正在准备本地工作空间，数据保存在这台电脑。" : "Starting your local workspace. Your data stays on this computer.";
document.getElementById("close").addEventListener("click", () => window.nanomuseDesktop?.windowAction("close"));
