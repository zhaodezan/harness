import json

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile

from backend.app.db import database_connection
from backend.app.plugin_store import MAX_PLUGIN_ARCHIVE_BYTES
from backend.app.skills import discover_skills

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def list_loaded_skills(request: Request):
    return [skill.to_dict() for skill in request.app.state.plugin_store.list_skills()]


@router.get("/migration-status")
def skill_migration_status(request: Request):
    return {"warnings": getattr(request.app.state, "plugin_migration_warnings", [])}


@router.post("/import", status_code=201)
async def import_skill(request: Request, file: UploadFile = File(...)):
    archive = await file.read(MAX_PLUGIN_ARCHIVE_BYTES + 1)
    await file.close()
    try:
        skills = request.app.state.plugin_store.import_skill_zip(archive)
    except OverflowError as error:
        raise HTTPException(status_code=413, detail=str(error)) from None
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return {"skills": [skill.to_dict() for skill in skills]}


@router.delete("/{skill_name}", status_code=204)
def delete_skill(skill_name: str, request: Request):
    with database_connection(request.app.state.database_path) as connection:
        agents = connection.execute("SELECT name, selected_skills_json FROM agents").fetchall()
    references = [
        row["name"] for row in agents
        if skill_name in json.loads(row["selected_skills_json"] or "[]")
    ]
    if references:
        raise HTTPException(status_code=409, detail=f"Skill 正被智能体使用：{', '.join(references)}")
    if not request.app.state.plugin_store.delete_skill(skill_name):
        raise HTTPException(status_code=404, detail="Skill 不存在")
    return None


@router.get("/discover")
def discover(directory: str = Query(min_length=1, max_length=2048)):
    try:
        skills = discover_skills(directory)
    except OSError:
        raise HTTPException(status_code=400, detail="无法读取这个 Skills 文件夹") from None
    return [skill.to_dict() for skill in skills]
