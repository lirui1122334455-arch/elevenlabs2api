from __future__ import annotations

import re
import secrets
import string


def is_valid_password(value: str) -> bool:
    password = value.strip()
    return (
        len(password) >= 8
        and bool(re.search(r"[A-Za-z]", password))
        and bool(re.search(r"[0-9]", password))
        and bool(re.search(r"[^A-Za-z0-9]", password))
    )


def generate_password(length: int = 20) -> str:
    if length < 12:
        raise ValueError("password length must be at least 12")
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "Aa9!" + "".join(secrets.choice(alphabet) for _ in range(length - 4))
        if is_valid_password(password):
            return password
