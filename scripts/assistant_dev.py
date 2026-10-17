"""Start the personal assistant in its own directory; research remains available."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from urllib.parse import quote

import uvicorn

# A shared interpreter may have an editable install of another checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nanomuse.assistant.capabilities import capabilities  # noqa: E402
from nanomuse.assistant.profile import assistant_settings, own_directory  # noqa: E402
from nanomuse.llm.mock import MockLLM  # noqa: E402
from nanomuse.logger import setup_logging  # noqa: E402
from nanomuse.server import create_app  # noqa: E402
from nanomuse.server.service import MuseService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(".local/assistant-dev"))
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--offline", action="store_true", help="Use labelled scripted responses.")
    parser.add_argument("--check", action="store_true", help="Print inventory without serving.")
    parser.add_argument(
        "--desktop-control", action="store_true", help="Stop when the owning desktop closes stdin."
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    directory = args.data_dir.expanduser().resolve()
    try:
        with own_directory(directory):
            settings = assistant_settings(directory, args.port, args.config)
            setup_logging("INFO", directory / "logs")
            service = MuseService(settings, llm=MockLLM([]) if args.offline else None)
            if args.check:
                print(json.dumps(capabilities(service), ensure_ascii=False, indent=2))
                return
            # The assistant opens at chat and keeps model setup visible in Connections.
            service.connections.set_onboarded(True)
            app = create_app(settings, service)
            url = f"http://127.0.0.1:{args.port}/?tab=chat"
            shortcut = directory / "open-assistant.url"
            shortcut.write_text(
                f"[InternetShortcut]\nURL={url}#token={quote(service.token, safe='')}\n",
                encoding="utf-8",
            )
            print(f"Assistant: {url}", flush=True)
            print(f"Private pairing shortcut: {shortcut}", flush=True)
            server = uvicorn.Server(
                uvicorn.Config(
                    app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False
                )
            )
            if args.desktop_control:
                # Only the parent owns this pipe. No HTTP shutdown endpoint is exposed.
                def control() -> None:
                    while line := sys.stdin.readline():
                        if line.strip() == "shutdown":
                            break
                    server.should_exit = True

                threading.Thread(target=control, daemon=True, name="desktop-control").start()
            server.run()
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
