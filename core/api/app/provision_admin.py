"""Provision the first password-based administrator during installation.

This module is intentionally not exposed over HTTP.  It is invoked by the
Ubuntu installer inside the API container and is idempotent: once a usable
administrator exists it never resets or replaces that account.
"""
from __future__ import annotations

import os
import sys

from .auth_utils import hash_password
from .platform import pool


def main() -> int:
    username = os.getenv("VISION_INITIAL_ADMIN_USERNAME", "innovation-admin").strip()
    password = os.getenv("VISION_INITIAL_ADMIN_PASSWORD", "")
    if not username or len(password) < 8:
        print("initial administrator credentials are not configured", file=sys.stderr)
        return 2

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(12062026)")
        cur.execute(
            "SELECT id,username FROM users WHERE active=true AND role='admin' AND password_hash LIKE 'scrypt$%%' LIMIT 1"
        )
        existing = cur.fetchone()
        if existing:
            conn.commit()
            print(f"password administrator already exists: {existing['username']}")
            return 0
        try:
            cur.execute(
                "INSERT INTO users(username,password_hash,role,active) VALUES (%s,%s,'admin',true) RETURNING id,username",
                (username, hash_password(password)),
            )
            user = cur.fetchone()
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                print("initial administrator username already exists without a usable password", file=sys.stderr)
                return 3
            raise
        cur.execute(
            "INSERT INTO audit_logs(actor_type,actor_id,action,object_type,object_id,metadata) VALUES ('SYSTEM',%s,'portal.initial_admin.provision','user',%s,'{}'::jsonb)",
            (str(user["id"]), str(user["id"])),
        )
        conn.commit()
    print(f"password administrator created: {username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
