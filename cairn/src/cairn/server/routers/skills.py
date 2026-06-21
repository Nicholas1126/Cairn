from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from cairn import skills_store
from cairn.server.models import SkillContent, SkillCreate, SkillEnable, SkillInfo

router = APIRouter(tags=["skills"])


def _info(meta: skills_store.SkillMeta) -> SkillInfo:
    return SkillInfo(name=meta.name, description=meta.description, enabled=meta.enabled)


def _find(name: str) -> skills_store.SkillMeta:
    for m in skills_store.list_skills():
        if m.name == name:
            return m
    raise HTTPException(404, f"skill not found: {name}")


@router.get("/skills", response_model=list[SkillInfo])
def list_skills():
    return [_info(m) for m in skills_store.list_skills()]


@router.get("/skills/{name}", response_model=SkillContent)
def get_skill(name: str):
    _find(name)
    return SkillContent(name=name, content=skills_store.read_skill_md(name))


@router.post("/skills", status_code=201, response_model=SkillInfo)
def create_skill(body: SkillCreate):
    try:
        skills_store.create_skill(body.name, body.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _info(_find(body.name))


@router.put("/skills/{name}", response_model=SkillInfo)
def update_skill(name: str, body: SkillContent):
    _find(name)
    skills_store.write_skill_md(name, body.content)
    return _info(_find(name))


@router.put("/skills/{name}/enabled", response_model=SkillInfo)
def set_enabled(name: str, body: SkillEnable):
    _find(name)
    skills_store.set_enabled(name, body.enabled)
    return _info(_find(name))


@router.delete("/skills/{name}", status_code=200)
def delete_skill(name: str):
    _find(name)
    skills_store.delete_skill(name)
    return {"deleted": name}


@router.post("/skills/upload", status_code=201, response_model=SkillInfo)
async def upload_skill(file: UploadFile):
    data = await file.read()
    try:
        name = skills_store.import_zip(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _info(_find(name))
