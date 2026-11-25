import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, field_validator

from backend.app.db import database_connection
from backend.app.knowledge_base import search_documents
from backend.app.knowledge_documents import (
    MAX_UPLOAD_BYTES,
    DocumentParseError,
    parse_document,
    safe_storage_extension,
)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge bases"])
MAX_DOCUMENTS_PER_BASE = 1000


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("知识库名称不能为空")
        return value


class KnowledgeBasePatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("知识库名称不能为空")
        return value


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("搜索内容不能为空")
        return value


def _document_response(row) -> dict:
    return {
        "id": row["id"],
        "knowledge_base_id": row["knowledge_base_id"],
        "filename": row["filename"],
        "extension": row["extension"],
        "char_count": row["char_count"],
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _knowledge_base_response(connection, row) -> dict:
    counts = connection.execute(
        "SELECT count(*) AS document_count, "
        "sum(CASE WHEN status='ready' THEN 1 ELSE 0 END) AS ready_count, "
        "sum(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count "
        "FROM knowledge_documents WHERE knowledge_base_id=?",
        (row["id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "name": row["name"],
        "document_count": counts["document_count"] or 0,
        "ready_count": counts["ready_count"] or 0,
        "failed_count": counts["failed_count"] or 0,
        "status": "ready" if not counts["failed_count"] else "has_errors",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _stored_file_path(data_dir: Path, stored_path: str) -> Path | None:
    if not stored_path:
        return None
    root = (data_dir / "knowledge-bases").resolve()
    path = (data_dir / stored_path).resolve()
    if root not in path.parents:
        return None
    return path


def _storage_directory(data_dir: Path, knowledge_base_id: str) -> Path:
    root = data_dir / "knowledge-bases"
    if root.is_symlink():
        raise HTTPException(status_code=409, detail="知识库存储目录不能是符号链接")
    directory = root / knowledge_base_id / "files"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise HTTPException(status_code=409, detail="知识库存储目录不能是符号链接")
    resolved_root = root.resolve()
    if resolved_root not in directory.resolve().parents:
        raise HTTPException(status_code=409, detail="知识库存储路径无效")
    return directory


@router.get("")
def list_knowledge_bases(request: Request) -> list[dict]:
    with database_connection(request.app.state.database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM knowledge_bases ORDER BY updated_at DESC, name COLLATE NOCASE"
        ).fetchall()
        return [_knowledge_base_response(connection, row) for row in rows]


@router.post("", status_code=201)
def create_knowledge_base(payload: KnowledgeBaseCreate, request: Request) -> dict:
    knowledge_base_id = uuid4().hex
    with database_connection(request.app.state.database_path) as connection:
        connection.execute(
            "INSERT INTO knowledge_bases(id,name) VALUES(?,?)",
            (knowledge_base_id, payload.name),
        )
        row = connection.execute(
            "SELECT * FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        return _knowledge_base_response(connection, row)


@router.get("/{knowledge_base_id}")
def get_knowledge_base(knowledge_base_id: str, request: Request) -> dict:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT * FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return _knowledge_base_response(connection, row)


@router.patch("/{knowledge_base_id}")
def rename_knowledge_base(
    knowledge_base_id: str, payload: KnowledgeBasePatch, request: Request
) -> dict:
    with database_connection(request.app.state.database_path) as connection:
        cursor = connection.execute(
            "UPDATE knowledge_bases SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (payload.name, knowledge_base_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="知识库不存在")
        row = connection.execute(
            "SELECT * FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        return _knowledge_base_response(connection, row)


@router.delete("/{knowledge_base_id}", status_code=204)
def delete_knowledge_base(knowledge_base_id: str, request: Request) -> Response:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT id FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        agents = []
        for agent in connection.execute(
            "SELECT name,selected_knowledge_bases_json FROM agents"
        ).fetchall():
            try:
                selected = json.loads(agent["selected_knowledge_bases_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                selected = [knowledge_base_id]
            if knowledge_base_id in selected:
                agents.append(agent["name"])
        if agents:
            raise HTTPException(
                status_code=409,
                detail=f"知识库仍被以下 Agent 使用，请先解除授权：{', '.join(agents)}",
            )
        connection.execute("DELETE FROM knowledge_bases WHERE id=?", (knowledge_base_id,))
    directory = request.app.state.data_dir / "knowledge-bases" / knowledge_base_id
    if directory.is_dir() and not directory.is_symlink():
        shutil.rmtree(directory, ignore_errors=True)
    return Response(status_code=204)


@router.get("/{knowledge_base_id}/documents")
def list_documents(knowledge_base_id: str, request: Request) -> list[dict]:
    with database_connection(request.app.state.database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        rows = connection.execute(
            "SELECT id,knowledge_base_id,filename,extension,char_count,status,error,created_at,updated_at "
            "FROM knowledge_documents WHERE knowledge_base_id=? ORDER BY created_at DESC,rowid DESC",
            (knowledge_base_id,),
        ).fetchall()
    return [_document_response(row) for row in rows]


@router.post("/{knowledge_base_id}/documents", status_code=201)
async def upload_documents(
    knowledge_base_id: str, request: Request, files: list[UploadFile] = File(...)
) -> list[dict]:
    if not files:
        raise HTTPException(status_code=422, detail="请选择要导入的文件")
    with database_connection(request.app.state.database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        current_count = connection.execute(
            "SELECT count(*) FROM knowledge_documents WHERE knowledge_base_id=?",
            (knowledge_base_id,),
        ).fetchone()[0]
    if exists is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if current_count + len(files) > MAX_DOCUMENTS_PER_BASE:
        raise HTTPException(status_code=413, detail="每个知识库最多保存 1,000 个文件")

    directory = _storage_directory(request.app.state.data_dir, knowledge_base_id)
    uploaded_rows = []
    for upload in files:
        raw_filename = upload.filename or ""
        if raw_filename != Path(raw_filename.replace("\\", "/")).name or ".." in raw_filename.replace("\\", "/").split("/"):
            raise HTTPException(status_code=422, detail="文件名不能包含路径")
        filename = raw_filename[:255]
        document_id = uuid4().hex
        extension = ""
        error = None
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="单个文件不能大于 20 MB")
        try:
            extension = safe_storage_extension(filename)
            extracted_text = parse_document(filename, content)
            status_value = "ready"
        except DocumentParseError as parse_error:
            extracted_text = ""
            status_value = "failed"
            error = str(parse_error)
            try:
                extension = safe_storage_extension(filename)
            except DocumentParseError:
                extension = Path(filename).suffix.lower()[:20]

        final_path = directory / f"{document_id}{extension}"
        relative_path = f"knowledge-bases/{knowledge_base_id}/files/{final_path.name}" if status_value == "ready" else ""
        try:
            if status_value == "ready":
                fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=directory)
                os.close(fd)
                temporary_path = Path(temporary_name)
                try:
                    temporary_path.write_bytes(content)
                    os.replace(temporary_path, final_path)
                finally:
                    temporary_path.unlink(missing_ok=True)
            with database_connection(request.app.state.database_path) as connection:
                connection.execute(
                    "INSERT INTO knowledge_documents(id,knowledge_base_id,filename,extension,stored_path,"
                    "extracted_text,char_count,status,error) VALUES(?,?,?,?,?,?,?,?,?)",
                    (document_id, knowledge_base_id, filename, extension, relative_path,
                     extracted_text, len(extracted_text), status_value, error),
                )
                connection.execute(
                    "UPDATE knowledge_bases SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (knowledge_base_id,),
                )
                row = connection.execute(
                    "SELECT id,knowledge_base_id,filename,extension,char_count,status,error,created_at,updated_at "
                    "FROM knowledge_documents WHERE id=?", (document_id,),
                ).fetchone()
                uploaded_rows.append(_document_response(row))
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
    return uploaded_rows


@router.delete("/{knowledge_base_id}/documents/{document_id}", status_code=204)
def delete_document(knowledge_base_id: str, document_id: str, request: Request) -> Response:
    with database_connection(request.app.state.database_path) as connection:
        row = connection.execute(
            "SELECT stored_path FROM knowledge_documents WHERE id=? AND knowledge_base_id=?",
            (document_id, knowledge_base_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        stored_path = row["stored_path"]
        connection.execute("DELETE FROM knowledge_documents WHERE id=?", (document_id,))
        connection.execute(
            "UPDATE knowledge_bases SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (knowledge_base_id,),
        )
    path = _stored_file_path(request.app.state.data_dir, stored_path)
    if path and path.is_file() and not path.is_symlink():
        path.unlink(missing_ok=True)
    return Response(status_code=204)


@router.post("/{knowledge_base_id}/search")
def search_knowledge_base(
    knowledge_base_id: str, payload: SearchRequest, request: Request
) -> list[dict]:
    with database_connection(request.app.state.database_path) as connection:
        base = connection.execute(
            "SELECT name FROM knowledge_bases WHERE id=?", (knowledge_base_id,)
        ).fetchone()
        if base is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        rows = connection.execute(
            "SELECT id,knowledge_base_id,filename,extracted_text FROM knowledge_documents "
            "WHERE knowledge_base_id=? AND status='ready'",
            (knowledge_base_id,),
        ).fetchall()
    documents = [
        {**dict(row), "knowledge_base_name": base["name"]}
        for row in rows
    ]
    return search_documents(payload.query, documents, payload.limit)
