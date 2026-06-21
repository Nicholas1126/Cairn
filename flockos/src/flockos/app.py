"""Unified FlockOS FastAPI app: cairn app as parent, flock dashboard mounted at /flock."""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from flock.core.orchestrator import Flock

from cairn.server.app import STATIC_DIR as CAIRN_STATIC
from cairn.server.app import app as cairn_app
from flockos.flock_app import build_flock_app

# Paths owned by this module — cleared and re-registered on each build_app() call.
_OWNED_PATHS = {"/", "/cairn", "/flock"}


def build_app(orchestrator: Flock | None = None):
    """Build and return the unified FlockOS FastAPI app.

    Uses cairn's module-level FastAPI app as the parent.  Mutates its route
    list in place — safe to call more than once (idempotent).

    Route layout
    ------------
    GET /          → 307 redirect to /flock/  (flock dashboard is FlockOS home)
    GET /cairn     → cairn SPA index.html     (JS uses absolute /engines, /static)
    /flock         → flock dashboard sub-app  (all flock routes live here)
    <all others>   → cairn's existing routes  (/engines, /projects, /skills, /static, …)
    """
    # ------------------------------------------------------------------ #
    # Idempotency: strip any routes/mounts we own before re-registering.  #
    # This covers both cairn's original GET / and any routes we added in  #
    # a previous build_app() call.                                        #
    # ------------------------------------------------------------------ #
    cairn_app.router.routes = [
        r for r in cairn_app.router.routes
        if getattr(r, "path", None) not in _OWNED_PATHS
    ]

    # ------------------------------------------------------------------ #
    # GET /  →  307 to /flock/                                            #
    # ------------------------------------------------------------------ #
    @cairn_app.get("/", include_in_schema=False)
    def _home():
        return RedirectResponse(url="/flock/", status_code=307)

    # ------------------------------------------------------------------ #
    # GET /cairn  →  cairn SPA                                            #
    # cairn's JS uses absolute paths (/engines, /static) so it resolves  #
    # correctly from the parent root even when navigated to via /cairn.   #
    # ------------------------------------------------------------------ #
    @cairn_app.get("/cairn", include_in_schema=False)
    def _cairn_home():
        return FileResponse(Path(CAIRN_STATIC) / "index.html")

    # ------------------------------------------------------------------ #
    # Mount flock dashboard sub-app at /flock                             #
    # ------------------------------------------------------------------ #
    flock = orchestrator or Flock("flockos")
    flock_app = build_flock_app(flock)
    cairn_app.mount("/flock", flock_app)

    return cairn_app
