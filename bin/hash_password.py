#!/usr/bin/env python3
"""
Standalone admin-password-hash generator.

Werkt zonder Flask app-init of DATA_PATH te hoeven instellen. Heeft
alleen werkzeug nodig (komt met Flask mee, of: `pip install werkzeug`).

Gebruik:
    python3 bin/hash_password.py
    (vul tweemaal een wachtwoord in)

Plak de uitvoer als ADMIN_PASSWORD_HASH in je .env (lokaal) en in
Coolify > Environment Variables (productie).
"""
from __future__ import annotations

import getpass
import sys


def main() -> int:
    try:
        from werkzeug.security import generate_password_hash
    except ImportError:
        print("werkzeug ontbreekt. Installeer met:  pip install werkzeug", file=sys.stderr)
        return 1

    print("Admin-wachtwoord aanmaken voor Sluiskade")
    print("-" * 50)
    pw1 = getpass.getpass("Wachtwoord:        ")
    pw2 = getpass.getpass("Wachtwoord (herh): ")

    if pw1 != pw2:
        print("\nDe wachtwoorden komen niet overeen.", file=sys.stderr)
        return 2
    if len(pw1) < 10:
        print("\nWachtwoord moet minimaal 10 tekens zijn.", file=sys.stderr)
        return 3

    h = generate_password_hash(pw1, method="pbkdf2:sha256", salt_length=16)
    print()
    print("Voeg deze regel toe aan .env (en aan Coolify):")
    print()
    print(f"  ADMIN_PASSWORD_HASH={h}")
    print()
    print("Het plaintext wachtwoord staat nergens opgeslagen, alleen deze hash.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
