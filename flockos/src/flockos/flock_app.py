"""Build flock's dashboard FastAPI app WITHOUT starting uvicorn or npm.

Mirrors the component wiring in
``flock.orchestrator.server_manager.ServerManager._serve_dashboard`` but:

* does NOT call ``DashboardLauncher`` (no npm process / browser launch),
* does NOT call ``service.run_async`` / ``service.run`` (no uvicorn),
* does NOT require an initialized orchestrator runtime — route registration
  only needs ``orchestrator.store`` / ``orchestrator._event_emitter`` which are
  populated by ``Flock.__init__``.

The prebuilt frontend is served from ``flockos/static/flock``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from flock.api.base_service import BaseHTTPService
from flock.api.collector import DashboardEventCollector
from flock.api.graph_builder import GraphAssembler
from flock.api.websocket import WebSocketManager
from flock.components.server import (
    AgentsServerComponent,
    AgentsServerComponentConfig,
    ArtifactComponentConfig,
    ArtifactsComponent,
    ControlRoutesComponent,
    ControlRoutesComponentConfig,
    CORSComponent,
    CORSComponentConfig,
    HealthAndMetricsComponent,
    StaticFilesComponentConfig,
    StaticFilesServerComponent,
    ThemesComponent,
    ThemesComponentConfig,
    WebSocketComponentConfig,
    WebSocketServerComponent,
)

# flockos/static/flock — parents[2] == the flockos package root (.../flockos)
STATIC_DIR = Path(__file__).resolve().parents[2] / "static" / "flock"


def build_flock_app(orchestrator, *, static_dir: Path | None = None) -> FastAPI:
    """Build and return the configured flock dashboard FastAPI app.

    Args:
        orchestrator: A ``flock.core.orchestrator.Flock`` instance.
        static_dir: Directory to serve the prebuilt frontend from. Defaults to
            ``flockos/static/flock``.

    Returns:
        The configured ``FastAPI`` application (``service.app``).
    """
    static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR

    # StaticFilesServerComponent raises if the directory is missing. The real
    # Vite build lands here in a later task; until then drop a placeholder so
    # the catch-all static mount can be registered.
    static_dir.mkdir(parents=True, exist_ok=True)
    if not (static_dir / "index.html").exists():
        (static_dir / "index.html").write_text(
            "<!doctype html><title>FlockOS</title>"
            "<p>flock dashboard placeholder</p>"
        )

    # Wire the WebSocket manager + dashboard event collector exactly as
    # _serve_dashboard does (heartbeat disabled — no live server here).
    websocket_manager = WebSocketManager(
        enable_heartbeat=False, heartbeat_interval=120
    )
    event_collector = DashboardEventCollector(store=orchestrator.store)
    event_collector.set_websocket_manager(manager=websocket_manager)
    orchestrator._dashboard_collector = event_collector
    orchestrator._websocket_manager = websocket_manager
    orchestrator._event_emitter.set_websocket_manager(websocket_manager)

    service = BaseHTTPService(
        orchestrator=orchestrator,
        version="0.5.0",
    ).add_components(
        components=[
            HealthAndMetricsComponent(name="health_internal"),
            AgentsServerComponent(
                name="agents_internal",
                config=AgentsServerComponentConfig(
                    enabled=True, prefix="/api/v1/", tags=["Agents", "Public API"]
                ),
            ),
            ControlRoutesComponent(
                name="api_internal",
                config=ControlRoutesComponentConfig(
                    enabled=True, prefix="/api/", tags=["Control", "Public API"]
                ),
                graph_assembler=GraphAssembler(
                    store=orchestrator.store,
                    collector=event_collector,
                    orchestrator=orchestrator,
                ),
            ),
            ArtifactsComponent(
                name="artifacts_internal",
                config=ArtifactComponentConfig(
                    enabled=True, prefix="/api/v1/", tags=["Artifacts", "Public API"]
                ),
            ),
            WebSocketServerComponent(
                name="websocket_internal",
                config=WebSocketComponentConfig(
                    enabled=True,
                    enable_heartbeat=False,
                    hearbeat_interval=120,
                    prefix="/",
                    tags=["WebSocket", "Public API"],
                ),
            ),
            CORSComponent(
                name="cors_internal",
                config=CORSComponentConfig(
                    enabled=True,
                    prefix="",
                    tags=["CORS"],
                    allow_origins=["*"],
                    allow_credentials=True,
                    allow_methods=["*"],
                    allow_headers=["*"],
                ),
            ),
            ThemesComponent(
                name="themes_internal",
                themes_dir=None,
                config=ThemesComponentConfig(
                    enabled=True, prefix="/api/", tags=["Themes", "Public API"]
                ),
            ),
            StaticFilesServerComponent(
                name="static_files_internal",
                config=StaticFilesComponentConfig(
                    enabled=True,
                    prefix="",
                    tags=["Themes", "Public API"],
                    static_files_path=static_dir,
                ),
            ),
        ]
    )

    # configure() populates service.app and returns None.
    service.configure()
    return service.app
