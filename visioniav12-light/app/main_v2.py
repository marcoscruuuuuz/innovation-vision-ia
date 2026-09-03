from __future__ import annotations

import os
from typing import Annotated, Iterator

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from main import app, authorized_log, current_user, get_db, minio_client
from models import User

MEDIA_PATH = "/api/logs/{log_id}/media/{kind}"
app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != MEDIA_PATH]


def object_stream(response) -> Iterator[bytes]:  # type: ignore[no-untyped-def]
    try:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            yield chunk
    finally:
        response.close()
        response.release_conn()


@app.get(MEDIA_PATH)
def media_proxy(
    log_id: str,
    kind: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    if kind not in {"snapshot", "clip"}:
        raise HTTPException(404, "media not found")

    log = authorized_log(db, user, log_id)
    object_name = log.snapshot_object if kind == "snapshot" else log.clip_object
    if not object_name:
        raise HTTPException(404, "media not available")

    client = minio_client()
    bucket = os.getenv("MINIO_BUCKET", "vision-light")
    try:
        stat = client.stat_object(bucket, object_name)
        response = client.get_object(bucket, object_name)
    except Exception as exc:
        raise HTTPException(404, "media object not found") from exc

    media_type = stat.content_type or ("image/jpeg" if kind == "snapshot" else "video/mp4")
    extension = "jpg" if kind == "snapshot" else "mp4"
    headers = {
        "Content-Disposition": f'inline; filename="{log.event_key}-{log.id}.{extension}"',
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if stat.size is not None:
        headers["Content-Length"] = str(stat.size)

    return StreamingResponse(object_stream(response), media_type=media_type, headers=headers)
