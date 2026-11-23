# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from novaport import __version__

router = APIRouter(prefix="/api", tags=["health"])

READY_TIMEOUT = 3.0


class Health(BaseModel):
    status: str
    version: str


@router.get("/healthz")
async def healthz() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/readyz", responses={503: {"model": Health}})
async def readyz(request: Request, response: Response) -> Health:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with asyncio.timeout(READY_TIMEOUT), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="database unavailable", version=__version__)
    return Health(status="ok", version=__version__)
