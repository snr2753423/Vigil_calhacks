"""Generate the Snowflake RSA key pair into ./secrets/ and print the SQL to register it.

uv run python scripts/gen_snowflake_keypair.py            # keeps an existing key
uv run python scripts/gen_snowflake_keypair.py --force    # rotates (re-run the SQL!)
uv run python scripts/gen_snowflake_keypair.py --user me  # another user (exact, case-sensitive)
"""

import argparse
import base64
import hashlib
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import BACKEND_DIR, ROOT_DIR, get_settings

SETUP_SQL = BACKEND_DIR / "sql" / "snowflake" / "001_setup.sql"


def generate_private_key_pem() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_key_der(private_pem: bytes) -> bytes:
    key = serialization.load_pem_private_key(private_pem, password=None)
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def public_key_body(private_pem: bytes) -> str:
    """Base64 DER without PEM headers/newlines: the value Snowflake's RSA_PUBLIC_KEY expects."""
    return base64.b64encode(public_key_der(private_pem)).decode()


def public_key_fingerprint(private_pem: bytes) -> str:
    """Matches RSA_PUBLIC_KEY_FP in `DESC USER` and the JWT `iss` suffix."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(public_key_der(private_pem)).digest()).decode()


def alter_user_sql(body: str, user: str | None) -> str:
    # The setup block ends as VIGIL_ROLE, which cannot alter users: switch back first.
    # User names are matched exactly (quoted): trial user names can be lowercase and case-sensitive.
    name = f"'\"{user}\"'" if user else "'\"' || CURRENT_USER() || '\"'"
    return (
        f"USE ROLE ACCOUNTADMIN;\nSET vigil_user = {name};\n"
        f"ALTER USER IDENTIFIER($vigil_user) SET RSA_PUBLIC_KEY = '{body}';"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--force", action="store_true", help="overwrite an existing key pair")
    parser.add_argument("--user", help="exact Snowflake user name (default: CURRENT_USER() of the Snowsight session)")
    args = parser.parse_args()

    settings = get_settings()
    private_path = settings.snowflake_private_key_file
    public_path = private_path.with_suffix(".pub")

    if private_path.exists() and not args.force:
        print(f"Using existing private key {private_path} (pass --force to rotate).")
        private_pem = private_path.read_bytes()
    else:
        if not private_path.parent.exists():  # only tighten a directory we create (never the repo root, $HOME, /tmp)
            private_path.parent.mkdir(parents=True, mode=0o700)
        private_pem = generate_private_key_pem()
        # Create the file 0600 from the start so the key is never briefly world-readable.
        fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(private_pem)
        os.chmod(private_path, 0o600)  # an existing file (--force) keeps its old mode otherwise
        print(f"Wrote private key {private_path} (chmod 600, gitignored).")

    public_pem = serialization.load_der_public_key(public_key_der(private_pem)).public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path.write_bytes(public_pem)
    print(f"Wrote public key  {public_path}")
    print(f"Fingerprint       {public_key_fingerprint(private_pem)}")

    rel_key = private_path.relative_to(ROOT_DIR) if private_path.is_relative_to(ROOT_DIR) else private_path
    user = args.user
    rule = "-" * 78
    print(f"\n{rule}\n1) In Snowsight, run as ACCOUNTADMIN while logged in as the Vigil user:\n{rule}")
    print(SETUP_SQL.read_text().strip())
    print(f"\n{rule}\n2) Register the public key (same worksheet):\n{rule}")
    print(alter_user_sql(public_key_body(private_pem), user))
    print("\n-- Verify: RSA_PUBLIC_KEY_FP should equal the fingerprint printed above.")
    print("DESC USER IDENTIFIER($vigil_user);")
    print(f"\n{rule}\n3) In .env set SNOWFLAKE_PRIVATE_KEY_PATH={rel_key}, then run `make check`.\n{rule}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
