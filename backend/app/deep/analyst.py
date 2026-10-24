"""Ask the facility via Cortex Analyst REST (SPEC §9 ⑧).

1. The semantic model `sql/snowflake/005_semantic.yaml` (VIGIL.CORE tables, facility-time dimensions, the five
   verified queries) is uploaded to `@CORE.SEMANTIC/vigil.yaml` with `PUT` on first use and whenever the file
   changes (Python connector; the SQL API has no PUT).
2. `POST /api/v2/cortex/analyst/message` with the question (and the conversation so far) returns the interpretation,
   the generated SQL (or the verified query it used) and suggestions when the question is unclear.
3. The SQL runs read-only through the SQL REST API (`POST /api/v2/statements`), capped at MAX_ROWS rows.
4. `chart_for` picks a chart from the result's shape (time + number → line, category + number → bar, two categories +
   number → grouped bar), or none.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import BACKEND_DIR
from app.db import snowflake_sql as sf
from app.db.snowflake_rest import SEMANTIC_MODEL_FILE
from app.db.snowflake_rest import client as rest_client

log = logging.getLogger(__name__)

SEMANTIC_YAML = BACKEND_DIR / "sql" / "snowflake" / "005_semantic.yaml"
STAGE = "@CORE.SEMANTIC"
STAGE_NAME = "vigil.yaml"
ANALYST_TIMEOUT_S = 60.0
EXEC_TIMEOUT_S = 45.0
MAX_ROWS = 500
MAX_HISTORY = 6  # earlier messages sent back to Analyst for follow-up questions

SUGGESTED = [
    "Which residents had more than 3 bed exits last night?",
    "Average night heart rate for Memory Care over the past week.",
    "Which residents were checked least often overnight?",
    "Alerts by unit and tier this week.",
    "Residents whose respiratory rate rose more than 15% vs their 7-day average.",
]

_uploaded: dict[str, str] = {}  # sha256 of the YAML this process uploaded
_upload_lock = asyncio.Lock()


def yaml_hash(path: Path = SEMANTIC_YAML) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _put_yaml(path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="vigil-semantic-") as tmp:
        local = Path(tmp) / STAGE_NAME  # the staged file takes the local file's name
        shutil.copyfile(path, local)

        def go(conn) -> None:
            sf.query(conn, f"PUT 'file://{local.as_posix()}' {STAGE} AUTO_COMPRESS = FALSE OVERWRITE = TRUE",
                     feature="analyst.put_semantic_model")  # fmt: skip

        sf.shared.run(go)


async def ensure_semantic_model(force: bool = False) -> str:
    """Upload the YAML if this process hasn't uploaded this version yet. → its sha256."""
    digest = yaml_hash()
    async with _upload_lock:
        if force or _uploaded.get("sha") != digest:
            t0 = time.perf_counter()
            await asyncio.to_thread(_put_yaml, SEMANTIC_YAML)
            _uploaded["sha"] = digest
            log.info("analyst: uploaded %s (%s…) in %.1f s", SEMANTIC_MODEL_FILE, digest[:8], time.perf_counter() - t0)
    return digest


# --------------------------------------------------------------------------- safety + charts (pure)

_READ_ONLY = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
_WRITE = re.compile(r"\b(insert|update|delete|merge|create|alter|drop|truncate|grant|revoke|call|put|copy|use)\b",
                    re.IGNORECASE)  # fmt: skip


_TOKENS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", re.S)


def strip_comments(sql: str) -> str:
    """Remove -- and /* */ comments; quoted strings and identifiers (which may contain '--') are kept as they are."""
    return _TOKENS.sub(lambda m: m.group(0) if m.group(0)[0] in "'\"" else " ", sql)


def check_read_only(sql: str) -> str:
    """The statement to run: one SELECT/WITH, comments and the trailing ';' removed. Raises ValueError otherwise."""
    body = strip_comments(sql).strip().rstrip(";").strip()
    if ";" in body:
        raise ValueError("more than one statement")
    if not _READ_ONLY.match(body):
        raise ValueError("not a SELECT")
    if _WRITE.search(re.sub(r"'[^']*'", "''", body)):
        raise ValueError("not read-only")
    return body


NUMERIC = {"fixed", "real"}
TEMPORAL = {"date", "timestamp_ntz", "timestamp_ltz", "timestamp_tz", "time"}


def chart_for(columns: list[dict], rows: list[list]) -> dict | None:
    """{type: line|bar, x, y, series?} from the result's shape, or None (nothing worth charting)."""
    if not rows or len(columns) < 2:
        return None
    idx_num = [i for i, c in enumerate(columns) if c["type"] in NUMERIC]
    idx_time = [i for i, c in enumerate(columns) if c["type"] in TEMPORAL]
    idx_cat = [i for i, c in enumerate(columns) if c["type"] not in NUMERIC | TEMPORAL]
    if not idx_num:
        return None
    y = idx_num[-1]  # the measure is usually last (COUNT, AVG, pct_change)
    # an ID-like first numeric column is a category, not the measure
    if len(idx_num) > 1 and columns[idx_num[0]]["name"].lower().endswith("_id"):
        idx_cat.insert(0, idx_num[0])
    if idx_time:
        return {"type": "line", "x": columns[idx_time[0]]["name"], "y": columns[y]["name"]}
    if len(idx_cat) >= 2 and len(rows) <= 60:
        a, b = idx_cat[0], idx_cat[1]
        xs = {r[a] for r in rows}
        if len(rows) > len(xs) and len(xs) <= 12 and len({r[b] for r in rows}) <= 8:  # x repeats: one bar per series
            return {"type": "bar", "x": columns[a]["name"], "y": columns[y]["name"], "series": columns[b]["name"]}
    if idx_cat and len(rows) <= 40:
        return {"type": "bar", "x": columns[idx_cat[0]]["name"], "y": columns[y]["name"]}
    return None


def parse_message(body: dict) -> dict:
    """Cortex Analyst's reply → {interpretation, sql, verified_query, suggestions, warnings}."""
    out: dict[str, Any] = {"interpretation": "", "sql": None, "verified_query": None, "suggestions": [],
                           "warnings": [w.get("message", str(w)) for w in body.get("warnings") or []]}  # fmt: skip
    texts = []
    for item in (body.get("message") or {}).get("content") or []:
        t = item.get("type")
        if t == "text":
            texts.append(item.get("text") or "")
        elif t == "sql":
            out["sql"] = item.get("statement")
            vq = (item.get("confidence") or {}).get("verified_query_used")
            if vq:
                out["verified_query"] = {"name": vq.get("name"), "question": vq.get("question"),
                                         "verified_by": vq.get("verified_by")}  # fmt: skip
        elif t == "suggestions":
            out["suggestions"] = list(item.get("suggestions") or [])
    out["interpretation"] = "\n\n".join(t for t in texts if t).strip()
    return out


# --------------------------------------------------------------------------- ask


async def ask(question: str, history: list[dict] | None = None) -> dict:
    """One question → Analyst's interpretation and SQL → the result table and a chart."""
    t0 = time.perf_counter()
    await ensure_semantic_model()
    messages = []
    for m in (history or [])[-MAX_HISTORY:]:
        role = "analyst" if m.get("role") == "analyst" else "user"
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": [{"type": "text", "text": content.strip()}]})
    if messages and messages[-1]["role"] == "user":
        messages.pop()  # Analyst wants user/analyst turns to alternate, ending with the new question
    messages.append({"role": "user", "content": [{"type": "text", "text": question}]})
    rest = rest_client()
    body = await rest.analyst_message(messages, timeout=ANALYST_TIMEOUT_S)
    analyst_ms = round((time.perf_counter() - t0) * 1000)
    reply = parse_message(body)
    result: dict[str, Any] = {
        "question": question,
        **reply,
        "request_id": body.get("request_id"),
        "analyst_ms": analyst_ms,
        "models": (body.get("response_metadata") or {}).get("model_names") or [],
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "chart": None,
        "error": None,
    }
    if not reply["sql"]:
        result["total_ms"] = analyst_ms
        return result
    try:
        statement = check_read_only(reply["sql"])
    except ValueError as exc:
        result["error"] = f"The generated SQL was not run ({exc})."
        return result
    t1 = time.perf_counter()
    try:
        columns, rows = await rest.sql_table(statement, feature="sql_api.analyst_exec", timeout=EXEC_TIMEOUT_S,
                                             max_rows=MAX_ROWS + 1)  # fmt: skip
    except Exception as exc:
        result["error"] = f"Running the SQL failed: {' '.join(str(exc).split())[:200]}"
        return result
    result["exec_ms"] = round((time.perf_counter() - t1) * 1000)
    result["truncated"] = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    result |= {"columns": columns, "rows": rows, "row_count": len(rows), "chart": chart_for(columns, rows)}
    result["total_ms"] = round((time.perf_counter() - t0) * 1000)
    return result
