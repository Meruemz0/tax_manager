"""Bind the encryption key to the database, including after a database restore."""

from tax_manager.security.crypto import decrypt_bytes, encrypt_bytes

_CHALLENGE = b"tax-manager-data-key-v1"
_AAD = b"tax-manager:data-key-verification:v1"


def verify_or_initialize_data_key(conn, key: bytes) -> None:
    # A transaction-scoped lock handles simultaneous worker startup.
    conn.execute("SELECT pg_advisory_xact_lock(7148221080)")
    row = conn.execute(
        "SELECT challenge_ciphertext FROM data_key_verification WHERE id = 1"
    ).fetchone()
    if row:
        try:
            value = decrypt_bytes(key, row["challenge_ciphertext"], _AAD)
        except ValueError as exc:
            raise RuntimeError("数据库加密密钥不匹配，请恢复对应的密钥备份") from exc
        if value != _CHALLENGE:
            raise RuntimeError("数据库加密密钥校验失败")
        return
    existing = conn.execute(
        """SELECT
             EXISTS(SELECT 1 FROM customer_system_accounts)
             OR EXISTS(SELECT 1 FROM order_vouchers)
             AS has_encrypted_data"""
    ).fetchone()
    if existing["has_encrypted_data"]:
        raise RuntimeError("数据库已有加密数据但没有密钥校验记录，请恢复对应的密钥备份")
    conn.execute(
        "INSERT INTO data_key_verification (id, challenge_ciphertext) VALUES (1, %s)",
        (encrypt_bytes(key, _CHALLENGE, _AAD),),
    )
