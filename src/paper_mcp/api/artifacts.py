"""Serve bundle artifacts: figure images, the full markdown, the zip.

`GET /a/{token}/{path}` is public and takes a caller-supplied path, so the
store's traversal guards are the security boundary here, not decoration.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from paper_mcp.models import InvalidArgumentError
from paper_mcp.tools.extract import artifact_store

router = APIRouter()


@router.get("/a/{token}/{rel:path}")
async def get_artifact(token: str, rel: str) -> FileResponse:
    """Return one file from a bundle.

    A rejected path and an unknown token both surface as 404: distinguishing
    them would tell an unauthenticated caller which bundles exist.
    """
    try:
        path = artifact_store().resolve(token, rel)
    except InvalidArgumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path)
