# -*- mode: python ; coding: utf-8 -*-
"""Standalone assistant bundle. Build separately from the unchanged Harness runtime."""
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
hidden = collect_submodules("nanomuse") + collect_submodules("uvicorn")
hidden += collect_submodules("websockets") + collect_submodules("anyio")
hidden += ["assistant_dev", "pydantic.deprecated.decorator", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont"]
datas = collect_data_files("nanomuse")
datas += [(os.path.join(ROOT, "config", "config.example.toml"), "nanomuse")]
datas += copy_metadata("nanomuse")
binaries = []
for package in ("certifi", "ddgs", "tzdata", "mcp", "html2text", "qrcode", "pywebpush", "py_vapid", "pyautogui", "pyscreeze", "pymsgbox", "pytweening", "mouseinfo", "pyperclip", "mss", "playwright"):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hidden += imports
a = Analysis(
    [os.path.join(SPECPATH, "assistant_entry.py")],
    pathex=[ROOT, os.path.join(ROOT, "scripts")], binaries=binaries, datas=datas,
    hiddenimports=sorted(set(hidden)), hookspath=[], runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "pandas", "IPython", "jupyter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="assistant-runtime", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="assistant-runtime")
