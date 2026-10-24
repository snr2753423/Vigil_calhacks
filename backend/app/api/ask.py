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
    question = body.question.strip()
    try:
        result = await analyst.ask(question, [t.model_dump() for t in body.history])
    except SnowflakeNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        log.warning("ask: %s", exc)
        reason = " ".join(str(exc).split())[:160] or type(exc).__name__
        # Offline-safe: the last real answer to this question (from Snowflake, stored in Tiger), clearly labelled.
        if not body.history and (saved := await analyst.recall(question)) is not None:
            return saved | {"fallback": {"answered_at": saved.get("answered_at"), "reason": reason}}
        raise HTTPException(502, f"Cortex Analyst is unavailable right now ({reason}).") from exc
    if not body.history:
        await analyst.remember(result)
    return result
