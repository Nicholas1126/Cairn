from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cairn.dispatcher.runtime.local import resolve
from cairn.server.models import EngineInfo, EngineOverride, ToolInfo

router = APIRouter(tags=["engines"])


def _engine_info(worker_type: str) -> EngineInfo:
    binary = resolve.BINARY[worker_type]
    probe = resolve.probe_engine(worker_type)
    ov_raw = resolve.load_overrides().get(worker_type)
    override = None
    if isinstance(ov_raw, dict) and ov_raw.get("path"):
        override = EngineOverride(path=ov_raw["path"], launcher=ov_raw.get("launcher", "direct"))
    return EngineInfo(
        type=worker_type, binary=binary,
        launchable=probe["launchable"], path=probe["path"],
        version=probe["version"], source=probe["source"], override=override,
    )


@router.get("/engines", response_model=list[EngineInfo])
def list_engines():
    return [_engine_info(t) for t in resolve.BINARY]


@router.get("/tools", response_model=list[ToolInfo])
def list_tools():
    out = []
    for name in resolve.TOOLS:
        probe = resolve.probe_tool(name)
        out.append(ToolInfo(name=name, launchable=probe["launchable"],
                            version=probe["version"], path=probe["path"]))
    return out


@router.put("/engines/{worker_type}/override", response_model=EngineInfo)
def put_override(worker_type: str, body: EngineOverride):
    if worker_type not in resolve.BINARY:
        raise HTTPException(404, "Unknown engine type")
    resolve.set_override(worker_type, body.path, body.launcher)
    return _engine_info(worker_type)


@router.delete("/engines/{worker_type}/override", response_model=EngineInfo)
def delete_override(worker_type: str):
    if worker_type not in resolve.BINARY:
        raise HTTPException(404, "Unknown engine type")
    resolve.remove_override(worker_type)
    return _engine_info(worker_type)
