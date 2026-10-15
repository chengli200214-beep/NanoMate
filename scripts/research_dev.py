"""Serve the research workbench locally with its own settings and data directory."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote

import uvicorn

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nanomuse.config import load_settings  # noqa: E402
from nanomuse.logger import setup_logging  # noqa: E402
from nanomuse.research.contracts import Constraint, ProjectSpec  # noqa: E402
from nanomuse.research.sources.dns import DNS_MODES  # noqa: E402
from nanomuse.server import create_app  # noqa: E402
from nanomuse.server.service import MuseService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", type=Path, default=Path(".local/research-dev"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--source-dns", choices=DNS_MODES, default=None)
    parser.add_argument(
        "--seed-fixture", action="store_true", help="Create one labelled offline replay project."
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")

    data_dir = args.data_dir.expanduser().resolve()
    os.environ["NANOMUSE_DATA_DIR"] = str(data_dir)
    if args.source_dns is not None:
        os.environ["NANOMUSE_RESEARCH_DNS"] = args.source_dns
    settings = load_settings(args.config)
    settings.data_dir = data_dir
    settings.agent.workspace = data_dir / "workspace"
    settings.server.host, settings.server.port = "127.0.0.1", args.port
    settings.server.auth, settings.server.update_check = True, False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.hub.enabled = False
    settings.connectors.email.enabled = False
    settings.connectors.calendar.enabled = False
    settings.connectors.contacts.enabled = False
    settings.browser.enabled = settings.gui.enabled = settings.hands.enabled = False
    settings.skills.enabled = False
    settings.mcp.servers = []
    setup_logging("INFO", data_dir / "logs")
    service = MuseService(settings)
    # This dedicated entry point opens the research workspace without chat onboarding.
    # Live projects still require a configured model; fixture projects use MockLLM.
    service.connections.set_onboarded(True)
    app = create_app(settings, service)
    store = app.state.research.store
    if args.seed_fixture and not store.list("project"):
        project = store.create_project(
            ProjectSpec(
                title="Open Dots and nanoMuse / 持续技术选型",
                goal="Compare the saved public sources for a Windows portfolio project.",
                candidates=[
                    "https://github.com/Anil-matcha/open-dots",
                    "https://github.com/nano-muse/nanoMuse",
                ],
                constraints=[
                    Constraint(id="windows", text="Local development on Windows"),
                    Constraint(id="traceability", text="Versioned, traceable evidence"),
                ],
                mode="fixture",
            )
        )
        store.enqueue(project["id"], request_key="initial-fixture")

    url = f"http://127.0.0.1:{args.port}/?tab=research"
    private_link = f"{url}#token={quote(service.token, safe='')}"
    # Keep the pairing token out of terminal logs. The ignored link is for this machine.
    link_path = data_dir / "open-research.url"
    link_path.write_text(f"[InternetShortcut]\nURL={private_link}\n", encoding="utf-8")
    print(f"Research workspace: {url}", flush=True)
    print(f"Open the private pairing shortcut: {link_path}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
