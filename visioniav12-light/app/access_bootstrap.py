from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select

from main import app, pwd_context
from models import SessionLocal, User

CREDENTIAL_STATUS_FILE = Path(os.getenv("ACCESS_BOOTSTRAP_STATUS_FILE", "/tmp/vision-light-access-bootstrap.json"))


def parse_scope(raw: str) -> list[str]:
    return sorted({item.strip() for item in raw.split(",") if item.strip()})


@app.on_event("startup")
def provision_initial_client() -> None:
    """Create the initial client through the application model.

    Plaintext credentials are supplied by the local .env generated on the VM.
    They are never logged, returned by an API, or committed to Git.
    """

    email = os.getenv("INITIAL_CLIENT_EMAIL", "").strip().lower()
    password = os.getenv("INITIAL_CLIENT_PASSWORD", "")
    scope = parse_scope(os.getenv("INITIAL_CLIENT_CONDO_SCOPE", ""))
    status: dict[str, object] = {
        "admin_email_configured": bool(os.getenv("INITIAL_ADMIN_EMAIL")),
        "client_email_configured": bool(email),
        "client_scope": scope,
        "client_created": False,
    }

    if email and password and scope:
        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    password_hash=pwd_context.hash(password),
                    role="client",
                    active=True,
                    condo_scope=scope,
                )
                session.add(user)
                session.commit()
                status["client_created"] = True
                status["client_id"] = user.id
            else:
                status["client_created"] = False
                status["client_id"] = user.id
                status["client_exists"] = True

    CREDENTIAL_STATUS_FILE.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    CREDENTIAL_STATUS_FILE.chmod(0o600)
