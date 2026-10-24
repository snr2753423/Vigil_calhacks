"""POST /api/ask: Ask the facility (SPEC §9 ⑧, §11.4) via Cortex Analyst REST; GET the suggested questions."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.snowflake_sql import SnowflakeNotConfigured
from app.deep import analyst

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ask", tags=["ask"])


class Turn(BaseModel):
    role: str = Field(..., pattern="^(user|analyst)$")
    content: str = Field(..., max_length=4000)


class AskIn(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    history: list[Turn] = []


@router.get("")
async def suggestions() -> dict:
    return {"suggested": analyst.SUGGESTED, "semantic_model": "@VIGIL.CORE.SEMANTIC/vigil.yaml"}


@router.post("")
async def ask(body: AskIn) -> dict:
    try:
        return await analyst.ask(body.question.strip(), [t.model_dump() for t in body.history])
    except SnowflakeNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        log.warning("ask: %s", exc)
        raise HTTPException(
            502, f"Cortex Analyst is unavailable right now ({' '.join(str(exc).split())[:160]})."
        ) from exc
