"""Snowflake REST client: JWT claims, response parsing, the SQL-API fallback and the Cortex Search request."""

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.config import Settings
from app.db import snowflake_rest as sr


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_jwt_identity_upper_cases_login_name_and_strips_region() -> None:
    assert sr.jwt_identity("lmfnxit-tsb47909", "someone@gmail.com") == ("LMFNXIT-TSB47909", "SOMEONE@GMAIL.COM")
    assert sr.jwt_identity("xy12345.us-east-2.aws", "vigil_app") == ("XY12345", "VIGIL_APP")


def test_jwt_claims_and_signature(key: rsa.RSAPrivateKey) -> None:
    token = sr.make_jwt(key, "org-acct", "rudrangi", now=1_000_000)
    head, body, sig = token.split(".")
    claims = json.loads(_b64d(body))
    fp = sr.public_key_fingerprint(key)
    assert json.loads(_b64d(head)) == {"alg": "RS256", "typ": "JWT"}
    assert claims == {"iss": f"ORG-ACCT.RUDRANGI.{fp}", "sub": "ORG-ACCT.RUDRANGI", "iat": 1_000_000,
                      "exp": 1_003_600}  # fmt: skip
    assert fp.startswith("SHA256:")
    key.public_key().verify(_b64d(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_base_url_uses_hyphens() -> None:
    assert sr.base_url("ORG-My_Acct") == "https://org-my-acct.snowflakecomputing.com"


@pytest.mark.parametrize(
    "body,text",
    [
        ({"structured_output": [{"raw_message": {"a": 1}, "type": "json"}]}, '{"a": 1}'),
        ({"choices": [{"messages": '{"a": 2}'}]}, '{"a": 2}'),
        ({"choices": [{"message": {"content": "hi"}}]}, "hi"),
        ('data: {"choices":[{"delta":{"content":"he"}}]}\ndata: {"choices":[{"delta":{"content":"y"}}]}\n'
         "data: [DONE]", "hey"),
    ],
)  # fmt: skip
def test_parse_complete_body(body, text) -> None:
    assert sr.parse_complete_body(body) == text


def test_parse_json_text_tolerates_a_code_fence() -> None:
    assert sr.parse_json_text('```json\n{"headline": "x"}\n```') == {"headline": "x"}
    with pytest.raises(ValueError):
        sr.parse_json_text("[1, 2]")


def test_schema_in_prompt_appends_to_the_last_user_message() -> None:
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    out = sr.schema_in_prompt(msgs, {"type": "json", "schema": {"type": "object"}})
    assert msgs[1]["content"] == "u"  # not mutated
    assert (
        out[1]["content"].startswith("u\n\nRespond with ONLY one JSON object")
        and '{"type":"object"}' in out[1]["content"]
    )


def _client(tmp_path: Path, key: rsa.RSAPrivateKey, handler) -> sr.SnowflakeRest:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    (tmp_path / "k.p8").write_bytes(pem)
    settings = Settings(_env_file=None, snowflake_account="org-acct", snowflake_user="me@x.com",
                        snowflake_private_key_path=str(tmp_path / "k.p8"), cortex_model="claude-sonnet-5")  # fmt: skip
    rest = sr.SnowflakeRest(settings)
    rest._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=sr.base_url("org-acct"))
    return rest


def test_complete_falls_back_to_the_sql_api_when_the_endpoint_is_blocked(tmp_path, key) -> None:
    seen: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.url.path, body))
        assert req.headers["X-Snowflake-Authorization-Token-Type"] == "KEYPAIR_JWT"
        if req.url.path.endswith("inference:complete"):
            return httpx.Response(403, json={"code": "003001", "message": "This account is not allowed"})
        raw = {
            "model": "claude-sonnet-5",
            "choices": [{"messages": '{"headline": "ok"}'}],
            "usage": {"total_tokens": 9},
        }
        return httpx.Response(200, json={"data": [[json.dumps(raw)]]})

    async def go():
        rest = _client(tmp_path, key, handler)
        rest._client_loop = asyncio.get_running_loop()
        fmt = {"type": "json", "schema": {"type": "object"}}
        first = await rest.cortex_complete([{"role": "user", "content": "q"}], fmt, timeout=5)
        second = await rest.cortex_complete([{"role": "user", "content": "q"}], fmt, timeout=5)
        return first, second

    first, second = asyncio.run(go())
    assert first.json() == {"headline": "ok"} and first.via == "sql_api" and second.via == "sql_api"
    paths = [p for p, _ in seen]
    assert paths == ["/api/v2/cortex/inference:complete", "/api/v2/statements", "/api/v2/statements"]  # blocked once
    stmt = seen[1][1]
    assert "SNOWFLAKE.CORTEX.COMPLETE" in stmt["statement"] and stmt["role"] == "VIGIL_ROLE"
    messages = json.loads(stmt["bindings"]["2"]["value"])
    options = json.loads(stmt["bindings"]["3"]["value"])
    assert "Respond with ONLY one JSON object" in messages[-1]["content"]
    assert "response_format" not in options and "temperature" not in options


def test_other_errors_are_raised(tmp_path, key) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "390144", "message": "JWT token is invalid"})

    async def go():
        rest = _client(tmp_path, key, handler)
        rest._client_loop = asyncio.get_running_loop()
        await rest.cortex_complete([{"role": "user", "content": "q"}], timeout=5)

    with pytest.raises(sr.SnowflakeRestError) as err:
        asyncio.run(go())
    assert err.value.status == 401 and err.value.code == "390144"


def test_search_care_plan_filters_by_resident(tmp_path, key) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v2/databases/VIGIL/schemas/DOCS/cortex-search-services/CARE_PLAN_SEARCH:query"
        body = json.loads(req.content)
        assert body["filter"] == {"@eq": {"resident_id": 17}} and body["limit"] == 4
        return httpx.Response(200, json={"results": [{"chunk": "c", "section": "Escalation preferences",
                                                      "resident_id": "17"}]})  # fmt: skip

    async def go():
        rest = _client(tmp_path, key, handler)
        rest._client_loop = asyncio.get_running_loop()
        return await rest.search_care_plan(17, "q")

    assert asyncio.run(go()) == [{"section": "Escalation preferences", "chunk": "c", "resident_id": 17}]
