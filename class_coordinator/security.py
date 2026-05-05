from __future__ import annotations

import bcrypt

from .config import BCRYPT_ROUNDS


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            encoded.encode("ascii"),
        )
    except (ValueError, TypeError):
        return False
