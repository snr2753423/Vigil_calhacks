import base64
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_der_public_key

from app.config import ROOT_DIR, Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_snowflake_keypair as keygen  # noqa: E402


def test_relative_key_path_resolves_against_repo_root() -> None:
    s = Settings(_env_file=None, snowflake_private_key_path="secrets/k.p8")
    assert s.snowflake_private_key_file == ROOT_DIR / "secrets" / "k.p8"


def test_absolute_key_path_is_kept(tmp_path: Path) -> None:
    s = Settings(_env_file=None, snowflake_private_key_path=str(tmp_path / "k.p8"))
    assert s.snowflake_private_key_file == tmp_path / "k.p8"


def test_snowflake_configured_needs_account_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    # _env_file=None skips .env, but exported env vars still count: clear them for a hermetic test.
    monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)
    monkeypatch.delenv("SNOWFLAKE_USER", raising=False)
    assert not Settings(_env_file=None).snowflake_configured
    assert Settings(_env_file=None, snowflake_account="org-acct", snowflake_user="me").snowflake_configured


@pytest.fixture(scope="module")
def private_pem() -> bytes:
    return keygen.generate_private_key_pem()


def test_public_key_body_is_bare_base64_der(private_pem: bytes) -> None:
    body = keygen.public_key_body(private_pem)
    assert "BEGIN" not in body and "\n" not in body
    assert load_der_public_key(base64.b64decode(body)).key_size == 2048


def test_fingerprint_format(private_pem: bytes) -> None:
    fp = keygen.public_key_fingerprint(private_pem)
    assert fp.startswith("SHA256:")
    assert len(base64.b64decode(fp.removeprefix("SHA256:"))) == 32


def test_alter_user_sql() -> None:
    named = keygen.alter_user_sql("ABC", "me").splitlines()
    assert named == [
        "USE ROLE ACCOUNTADMIN;",
        "SET vigil_user = '\"me\"';",
        "ALTER USER IDENTIFIER($vigil_user) SET RSA_PUBLIC_KEY = 'ABC';",
    ]
    current = keygen.alter_user_sql("ABC", None).splitlines()
    assert current[1] == "SET vigil_user = '\"' || CURRENT_USER() || '\"';"
