# FlockOS

FlockOS unifies two relatively independent subsystems in one repository:

- **cairn** — fact/intent hypergraph engine: state-path search over a directed acyclic hypergraph, used to prove reachability / verifiability / correctness. Provided as an atomic capability.
- **flock** — declarative blackboard multi-agent orchestration: agents declare typed contracts (`.consumes(Type).publishes(Type)`) and workflows emerge from type subscriptions. Used to decompose complex tasks into units small enough for cairn to search/verify.

This document covers **phase 1**: vendoring flock, adapting it to run on cairn's host agent engines, a single unified web app, and a one-click launcher. The flock↔cairn HTTP artifact-verification handoff (auto-creating cairn projects from flock output) is **phase 2** and not yet implemented.

## Repository layout

```
Cairn/                      # repo root = uv workspace
├── cairn/                  # cairn subsystem (unchanged)
│   └── src/cairn/...
├── flock/                  # vendored flock framework
│   └── src/flock/...       #   incl. src/flock/frontend (React/Vite dashboard)
├── flockos/                # integration layer (the only new code)
│   ├── src/flockos/
│   │   ├── engine.py       # CairnAgentEngine + CairnConfig + cairn_agent
│   │   ├── flock_app.py    # builds flock's dashboard FastAPI app (no npm/uvicorn)
│   │   ├── app.py          # unified app: cairn parent + flock mounted at /flock
│   │   └── cli.py          # flockos start/stop/status launcher
│   ├── static/flock/       # prebuilt flock dashboard (Vite build output)
│   └── tests/
├── pyproject.toml          # uv workspace root (members: cairn, flock, flockos)
└── .python-version         # 3.12
```

## Setup

```bash
uv sync          # creates .venv (Python 3.12) with all three packages editable
```

Notes:
- flock's deprecated `opentelemetry-exporter-jaeger{,-proto-grpc}==1.21.0` deps were dropped (they pull an old grpcio with no Python 3.12 wheel; no flock code imports them — the modern OTLP exporter remains).
- flock's own dev test suite needs `pytest-mock` (in flock's dev dependency group, not synced by default). It is not required to run FlockOS.

## One-click launcher

```bash
uv run flockos start                 # daemonize on 127.0.0.1:8000 (PID + log under ~/.cairn/run)
uv run flockos start --foreground    # run in the foreground (logs to terminal)
uv run flockos start --port 8011     # custom port
uv run flockos stop                  # graceful SIGTERM, then SIGKILL fallback
uv run flockos status                # running? + pid
```

On first `start`, if `flockos/static/flock/index.html` is missing, the launcher runs `npm install && npm run build` in `flock/src/flock/frontend` and copies the build into `flockos/static/flock`. The committed prebuilt output means this normally does not run.

`FLOCKOS_HOME` overrides where the PID/log live (default `~/.cairn`).

## Route map (single port)

| Path | Serves |
|------|--------|
| `GET /` | 307 redirect → `/flock/` (flock dashboard is the FlockOS home) |
| `/flock/` | flock real-time dashboard (React/Vite, built with base `/flock/`) |
| `/flock/api/...`, `/flock/api/v1/...` | flock control-plane API |
| `/flock/ws` | flock dashboard WebSocket |
| `/flock/health` | flock health |
| `/cairn` | cairn SPA (its JS uses absolute `/engines`, `/static`, … which resolve at root) |
| `/engines`, `/projects`, `/skills`, `/chat`, `/hints`, `/intents`, `/export`, `/executions`, `/static` | cairn — unchanged |

The unified app uses cairn's FastAPI app as the parent (so cairn's absolute-path SPA keeps working unchanged) and mounts the flock dashboard app at `/flock`. The flock dashboard header has a "Cairn 控制台 →" link to `/cairn`.

## CairnAgentEngine — running flock agents on cairn engines

flock agents normally call an LLM (DSPy) or an external OpenClaw gateway. FlockOS adds `CairnAgentEngine`, a flock `EngineComponent` that instead runs a **cairn host agent CLI** (claude code / codex / opencode / pi) as a one-shot, structured-output call — reusing cairn's worker drivers and host process execution. It runs **only on the host** (never in docker) and uses no LLM directly.

How `evaluate()` works:
1. Build a prompt from the input artifacts' payloads + the output type's JSON schema ("respond with only the matching JSON object").
2. Resolve the cairn driver for the engine type, build the CLI argv, and run it via `LocalManagedProcess` on the host.
3. Parse the agent's stdout back into the declared Pydantic output type; on invalid JSON, re-ask with a strict-JSON reminder (bounded by `retries`).

### Usage

```python
from flock.core.orchestrator import Flock
from flockos import CairnConfig, cairn_agent
from pydantic import BaseModel

class Idea(BaseModel):
    topic: str

class Pizza(BaseModel):
    name: str
    toppings: list[str]

# Resolve cairn workers (engine type + API env) from a dispatch.yaml,
# or construct CairnConfig(workers={...}) directly with WorkerConfig objects.
cfg = CairnConfig.from_dispatch("dispatch.yaml")

flock = Flock("flockos")
chef = cairn_agent(flock, cfg, alias="claude", name="chef").consumes(Idea).publishes(Pizza)

artifacts = await flock.invoke(chef, Idea(topic="classic"))
# artifacts[0].payload == {"name": ..., "toppings": [...]}
```

`CairnConfig` maps an alias → cairn `WorkerConfig` (carrying engine `type` and the API `env`). `cairn_agent(...)` is the FlockOS analog of `flock.openclaw_agent(...)`; under the hood it is `flock.agent(name).with_engines(CairnAgentEngine(worker=...))`.

Phase-1 scope: single output type per agent (the common case). Multi-output groups / fan-out are not yet supported by `CairnAgentEngine`.

## Tests

```bash
uv run pytest flockos/tests -q     # CairnAgentEngine + unified app + launcher
uv run pytest cairn/tests -q       # cairn regression (unchanged)
```

## Phase 2 (not yet implemented)

cairn will expose HTTP endpoints (e.g. create-project) so flock orchestration can pass produced artifacts (`origin`, `goal`, `hints`, `project_root`, engine mode) to cairn, auto-create a task (equivalent to the UI), and receive the verification result back. The `flockos/` integration layer is where that handoff will live.
