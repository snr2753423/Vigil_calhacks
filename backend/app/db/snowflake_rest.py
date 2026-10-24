"""The only module that calls Snowflake REST APIs (Cortex inference, Cortex Search, Cortex Analyst, SQL API).

Auth is key-pair JWT (the same RSA key as the connector). The JWT names the account and the user's LOGIN_NAME
upper-cased, as the Python connector does: on this account the user NAME is a case-sensitive lowercase
identifier while SNOWFLAKE_USER holds the LOGIN_NAME, and Snowflake matches the JWT against the login name.
REST calls run as the user's DEFAULT_ROLE (001_setup.sql sets VIGIL_ROLE).

Every call is counted with its latency in `snowflake_sql.call_stats` under `rest.<feature>`.

`cortex_complete` calls `POST /api/v2/cortex/inference:complete`. Some accounts (trials) are refused that
endpoint ("003001 This account is not allowed to access this endpoint"); then the same messages go to
`SNOWFLAKE.CORTEX.COMPLETE` through the SQL REST API (`POST /api/v2/statements`), and the inference endpoint
is retried after ENDPOINT_RETRY_S. On that path the JSON schema is written into the prompt instead of passed
as COMPLETE's `response_format`: measured on this account, structured output made a ~3k-token assessment take
> 45 s (the SQL API goes async) versus ~10 s for the same prompt with the schema inline. Callers validate the
JSON either way. `temperature` is not sent: the newest Claude models in Cortex reject it ("malformed request").
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import Settings, get_settings
from app.db.snowflake_sql import SnowflakeNotConfigured, call_stats

log = logging.getLogger(__name__)

JWT_LIFETIME_S = 3600  # Snowflake's maximum
JWT_RENEW_S = 300  # mint a new token this long before the old one expires
ENDPOINT_RETRY_S = 1800.0
BLOCKED_CODE = "003001"
SEARCH_SERVICE = ("DOCS", "CARE_PLAN_SEARCH")
SEMANTIC_MODEL_FILE = "@VIGIL.CORE.SEMANTIC/vigil.yaml"  # Phase 9


class SnowflakeRestError(RuntimeError):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(f"HTTP {status}{f' [{code}]' if code else ''}: {message}")
        self.status = status
        self.code = code


# --------------------------------------------------------------------------- JWT


def jwt_identity(account: str, user: str) -> tuple[str, str]:
    """(ACCOUNT, USER) for the JWT claims: the account identifier without any region/cloud suffix and the
    login name, both upper-case (what the connector sends; works for case-sensitive lowercase user names
    because the JWT is matched against LOGIN_NAME)."""
    return account.split(".")[0].upper(), user.upper()


def public_key_fingerprint(key: rsa.RSAPrivateKey) -> str:
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(key: rsa.RSAPrivateKey, account: str, user: str, now: float, lifetime_s: int = JWT_LIFETIME_S) -> str:
    acct, usr = jwt_identity(account, user)
    claims = {
        "iss": f"{acct}.{usr}.{public_key_fingerprint(key)}",
        "sub": f"{acct}.{usr}",
        "iat": int(now),
        "exp": int(now) + lifetime_s,
    }
    head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64url(sig)}"


def base_url(account: str) -> str:
    """https://<account identifier>.snowflakecomputing.com (underscores are not valid in hostnames)."""
    return f"https://{account.lower().replace('_', '-')}.snowflakecomputing.com"


# --------------------------------------------------------------------------- response parsing


def parse_complete_body(body: Any) -> str:
    """Text of a Cortex completion: the inference REST response (JSON or SSE text) or COMPLETE's JSON
    (structured_output / choices)."""
    if isinstance(body, str):
        body = body.strip()
        if body.startswith("data:"):  # server-sent events: concatenate the deltas
            parts = []
            for line in body.splitlines():
                if line.startswith("data:") and (payload := line[5:].strip()) and payload != "[DONE]":
                    parts.append(parse_complete_body(json.loads(payload)))
            return "".join(parts)
        body = json.loads(body)
    if not isinstance(body, dict):
        raise ValueError(f"unexpected completion body: {str(body)[:200]}")
    if so := body.get("structured_output"):
        msg = so[0].get("raw_message")
        return msg if isinstance(msg, str) else json.dumps(msg)
    choices = body.get("choices") or []
    if choices:
        c = choices[0]
        for key in ("message", "delta"):
            if isinstance(c.get(key), dict):
                return c[key].get("content") or c[key].get("text") or ""
        if "messages" in c:
            return c["messages"] if isinstance(c["messages"], str) else json.dumps(c["messages"])
    raise ValueError(f"no completion text in body: {json.dumps(body)[:200]}")


def parse_json_text(text: str) -> dict:
    """The model's JSON object, tolerating a ```json fence."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise ValueError("completion is not a JSON object")
    return obj


def schema_in_prompt(messages: list[dict[str, str]], response_format: dict) -> list[dict[str, str]]:
    """Append the JSON schema to the last user message (the SQL-API path's stand-in for response_format)."""
    schema = json.dumps(response_format.get("schema", response_format), separators=(",", ":"))
    out = [dict(m) for m in messages]
    last = next(i for i in range(len(out) - 1, -1, -1) if out[i]["role"] == "user")
    out[last]["content"] += (
        f"\n\nRespond with ONLY one JSON object that matches this JSON schema (no prose, no code fences):\n{schema}"
    )
    return out


@dataclass
class Completion:
    text: str
    model: str
    via: str  # inference_rest | sql_api
    latency_ms: float
    usage: dict | None = None

    def json(self) -> dict:
        return parse_json_text(self.text)


# --------------------------------------------------------------------------- client


class SnowflakeRest:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._key: rsa.RSAPrivateKey | None = None
        self._token: tuple[str, float] | None = None
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self.inference_blocked_until = 0.0
        self.last_error: str | None = None

    # ---- plumbing

    def _jwt(self) -> str:
        s = self.settings
        if not s.snowflake_configured:
            raise SnowflakeNotConfigured("SNOWFLAKE_ACCOUNT / SNOWFLAKE_USER are not set in .env")
        now = time.time()
        if self._token and self._token[1] - JWT_RENEW_S > now:
            return self._token[0]
        if self._key is None:
            path = s.snowflake_private_key_file
            if not path.is_file():
                raise SnowflakeNotConfigured(f"private key not found at {path} (run `make keygen`)")
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise SnowflakeNotConfigured("the Snowflake private key is not an RSA key")
            self._key = key
        token = make_jwt(self._key, s.snowflake_account or "", s.snowflake_user or "", now)
        self._token = (token, now + JWT_LIFETIME_S)
        return token

    def _http(self) -> httpx.AsyncClient:
        """One pooled client per event loop (scripts call asyncio.run more than once)."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._client_loop is not loop or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=base_url(self.settings.snowflake_account or ""))
            self._client_loop = loop
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _post(self, path: str, body: dict, *, feature: str, timeout: float) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._jwt()}",
            "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "vigil/0.1",
        }
        try:
            with call_stats.timed(f"rest.{feature}"):
                resp = await self._http().post(path, json=body, headers=headers, timeout=timeout)
                if resp.status_code >= 400:
                    raise _error(resp)
        except Exception as exc:
            self.last_error = f"{feature}: {' '.join(str(exc).split())[:200] or type(exc).__name__}"
            raise
        return resp

    # ---- Cortex inference

    async def cortex_complete(
        self,
        messages: list[dict[str, str]],
        response_format: dict | None = None,
        *,
        model: str | None = None,
        max_tokens: int = 1500,
        timeout: float = 30.0,
    ) -> Completion:
        """One chat completion. `response_format` is Cortex's {"type": "json", "schema": {...}}."""
        model = model or self.settings.cortex_model
        start = time.perf_counter()
        if time.monotonic() >= self.inference_blocked_until:
            body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
            if response_format:
                body["response_format"] = response_format
            try:
                resp = await self._post("/api/v2/cortex/inference:complete", body, feature="inference_complete",
                                        timeout=timeout)  # fmt: skip
                text = parse_complete_body(resp.json() if "json" in resp.headers.get("content-type", "") else resp.text)
                return Completion(text, model, "inference_rest", (time.perf_counter() - start) * 1000)
            except SnowflakeRestError as exc:
                if exc.code != BLOCKED_CODE:
                    raise
                self.inference_blocked_until = time.monotonic() + ENDPOINT_RETRY_S
                log.warning("cortex: inference:complete is not enabled for this account; using the SQL API")
        remaining = max(1.0, timeout - (time.perf_counter() - start))
        if response_format:
            messages = schema_in_prompt(messages, response_format)
        options: dict[str, Any] = {"max_tokens": max_tokens}
        rows = await self.sql(
            "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, PARSE_JSON(?), PARSE_JSON(?))",
            [model, json.dumps(messages), json.dumps(options)],
            feature="sql_api.cortex_complete",
            timeout=remaining,
        )
        raw = json.loads(rows[0][0])
        return Completion(parse_complete_body(raw), raw.get("model", model), "sql_api",
                          (time.perf_counter() - start) * 1000, raw.get("usage"))  # fmt: skip

    # ---- SQL API

    async def sql(
        self, statement: str, params: list[Any] | None = None, *, feature: str = "sql_api", timeout: float = 30.0
    ) -> list[list[Any]]:
        """Run one statement through POST /api/v2/statements (synchronous; rows as strings)."""
        s = self.settings
        body: dict[str, Any] = {
            "statement": statement,
            "timeout": max(1, int(timeout)),
            "warehouse": s.snowflake_warehouse,
            "role": s.snowflake_role,
            "database": s.snowflake_database,
        }
        if params:
            body["bindings"] = {str(i): {"type": "TEXT", "value": str(v)} for i, v in enumerate(params, 1)}
        resp = await self._post("/api/v2/statements", body, feature=feature, timeout=timeout + 2)
        if resp.status_code == 202:  # still running past the API's synchronous window
            raise SnowflakeRestError(202, "statement did not finish in time")
        return resp.json().get("data") or []

    # ---- Cortex Search

    async def search_care_plan(
        self, resident_id: int, query: str, limit: int = 4, *, timeout: float = 5.0
    ) -> list[dict[str, Any]]:
        """Top care-plan chunks for one resident from DOCS.CARE_PLAN_SEARCH (REST :query, filtered)."""
        schema, service = SEARCH_SERVICE
        path = f"/api/v2/databases/{self.settings.snowflake_database}/schemas/{schema}/cortex-search-services/{service}:query"
        body = {
            "query": query,
            "columns": ["chunk", "section", "resident_id"],
            "filter": {"@eq": {"resident_id": resident_id}},
            "limit": limit,
        }
        resp = await self._post(path, body, feature="cortex_search", timeout=timeout)
        out = []
        for r in resp.json().get("results") or []:
            rid = r.get("resident_id")
            out.append({"section": r.get("section") or "", "chunk": r.get("chunk") or "",
                        "resident_id": int(rid) if rid is not None else resident_id})  # fmt: skip
        return out

    # ---- Cortex Analyst (Phase 9)

    async def analyst_message(
        self, messages: list[dict[str, Any]], *, semantic_model_file: str = SEMANTIC_MODEL_FILE, timeout: float = 30.0
    ) -> dict[str, Any]:
        """POST /api/v2/cortex/analyst/message. Wired in Phase 9 ("Ask the facility"); no caller yet."""
        body = {"messages": messages, "semantic_model_file": semantic_model_file}
        resp = await self._post("/api/v2/cortex/analyst/message", body, feature="cortex_analyst", timeout=timeout)
        return resp.json()

    def status(self) -> dict[str, Any]:
        blocked = self.inference_blocked_until > time.monotonic()
        return {
            "inference_endpoint": "blocked for this account (using SQL API)" if blocked else "ok/untested",
            "last_error": self.last_error,
        }


def _error(resp: httpx.Response) -> SnowflakeRestError:
    code, message = None, resp.text[:300]
    try:
        body = resp.json()
        code, message = body.get("code") or body.get("error_code"), body.get("message") or message
    except ValueError:
        pass
    return SnowflakeRestError(resp.status_code, " ".join(str(message).split()), code)


_client: SnowflakeRest | None = None


def client() -> SnowflakeRest:
    global _client
    if _client is None:
        _client = SnowflakeRest()
    return _client
