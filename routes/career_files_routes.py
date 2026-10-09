"""Career hub file endpoints — CV upload and example cover-letter pairs.

Registered onto the /api/career router by routes.career_routes. Files are
stored through services.career.personal_files so every chunk carries owner
metadata; the CV filename is remembered in the owner's career settings.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, field_validator

from services.career.personal_files import delete_personal_file, list_personal_files, store_personal_file
from services.career.settings import save_career_settings
from services.memory.skill_format import slugify

EXAMPLE_PREFIX = "cover-letter-example-"
CV_PREFIX = "cv-"


class ExamplePair(BaseModel):
    title: str
    jd_text: str
    letter_text: str

    @field_validator("title", "jd_text", "letter_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


def example_markdown(pair: ExamplePair) -> str:
    return (f"# {pair.title}\n\n## Job description\n\n{pair.jd_text}\n\n"
            f"## Cover letter\n\n{pair.letter_text}\n")


def register_file_routes(router: APIRouter, owner_of: Callable[[Request], Optional[str]], rag_manager) -> None:
    def _require_rag():
        if rag_manager is None:
            raise HTTPException(503, "RAG is not available — the embedding service is required to index files")
        return rag_manager

    @router.post("/cv")
    async def upload_cv(request: Request, file: UploadFile = File(...)):
        user = owner_of(request)
        rag = _require_rag()
        raw = await file.read()
        name = file.filename or "cv.txt"
        if not name.startswith(CV_PREFIX):
            name = f"{CV_PREFIX}{name}"
        try:
            out = store_personal_file(rag, user, name, raw)
        except ValueError as e:
            raise HTTPException(422, str(e))
        save_career_settings(user, {"cv_filename": out["filename"]})
        return {"filename": out["filename"], "chunks": out["chunks"]}

    @router.get("/examples")
    def list_examples(request: Request):
        files = list_personal_files(owner_of(request), EXAMPLE_PREFIX)
        return {"examples": [{"filename": f["filename"], "chunks": f.get("chunks", 0)} for f in files]}

    @router.post("/examples", status_code=201)
    def add_example(body: ExamplePair, request: Request):
        user = owner_of(request)
        rag = _require_rag()
        name = f"{EXAMPLE_PREFIX}{slugify(body.title)}.md"
        out = store_personal_file(rag, user, name, example_markdown(body).encode("utf-8"))
        return {"filename": out["filename"], "chunks": out["chunks"]}

    @router.delete("/examples/{filename}")
    def delete_example(filename: str, request: Request):
        if not filename.startswith(EXAMPLE_PREFIX):
            raise HTTPException(404, "Example not found")
        if not delete_personal_file(rag_manager, owner_of(request), filename):
            raise HTTPException(404, "Example not found")
        return {"deleted": True}
