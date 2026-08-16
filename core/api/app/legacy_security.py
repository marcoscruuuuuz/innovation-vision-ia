from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .platform import require_admin

LEGACY_ADMIN_PREFIXES = (
    "/api/v1/condominiums",
    "/api/v1/dvrs",
    "/api/v1/cameras",
)


async def legacy_admin_guard(request: Request, call_next):
    path = request.url.path
    if any(path == prefix or path.startswith(prefix + "/") for prefix in LEGACY_ADMIN_PREFIXES):
        try:
            require_admin(request.headers.get("authorization"))
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)
