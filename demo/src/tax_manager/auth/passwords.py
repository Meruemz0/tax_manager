"""Password format used by demo/init.sql and the account screen."""

import hashlib
import hmac
import secrets


N, R, P = 131072, 8, 1
MAX_MEMORY = 256 * 1024 * 1024
# Valid public test vector: keeps unknown-user response timing close to a real account.
DUMMY_HASH = (
    "scrypt$131072$8$1$00112233445566778899aabbccddeeff$"
    "0887b186c64591b322fc71a0228c7cbce886af601cb4195e10031bf87abc4e00"
)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=N, r=R, p=P,
        maxmem=MAX_MEMORY, dklen=32,
    )
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt_hex, digest_hex = stored.split("$")
        if (kind, n, r, p) != ("scrypt", str(N), str(R), str(P)):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=N, r=R, p=P,
            maxmem=MAX_MEMORY, dklen=32,
        )
        return hmac.compare_digest(actual, expected)
    except (AttributeError, UnicodeError, ValueError):
        return False
