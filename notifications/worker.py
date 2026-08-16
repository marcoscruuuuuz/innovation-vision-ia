import json
import os
import urllib.error
import urllib.request

import psycopg
import redis
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INPUT_STREAM = os.getenv("NOTIFICATION_STREAM", "vision:notifications")
CONSUMER_GROUP = os.getenv("NOTIFICATION_CONSUMER_GROUP", "vision-notifications")
CONSUMER_NAME = os.getenv("NOTIFICATION_CONSUMER_NAME", "notification-1")
EVOLUTION_ENABLED = os.getenv("EVOLUTION_ENABLED", "false").lower() == "true"
EVOLUTION_API_BASE_URL = os.getenv("EVOLUTION_API_BASE_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_SEND_PATH_TEMPLATE = os.getenv("EVOLUTION_SEND_PATH_TEMPLATE", "/message/sendText/{instance}")
EVOLUTION_TIMEOUT = float(os.getenv("EVOLUTION_TIMEOUT", "15"))

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_group():
    try:
        r.xgroup_create(INPUT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def send_whatsapp(instance: str | None, number: str, text: str):
    if not EVOLUTION_ENABLED:
        raise RuntimeError("Evolution adapter disabled")
    if not EVOLUTION_API_BASE_URL or not EVOLUTION_API_KEY or not instance:
        raise RuntimeError("Evolution adapter configuration incomplete")
    path = EVOLUTION_SEND_PATH_TEMPLATE.format(instance=instance)
    req = urllib.request.Request(
        EVOLUTION_API_BASE_URL + path,
        data=json.dumps({"number": number, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EVOLUTION_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Evolution HTTP {resp.status}: {body[:1000]}")
            return body[:4000]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"Evolution request failed: {exc}") from exc


def process(log_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.*,cam.name AS camera_name,c.name AS condominium_name
              FROM event_logs l JOIN cameras cam ON cam.id=l.camera_id
              JOIN condominiums c ON c.id=l.condominium_id
             WHERE l.id=%s AND l.client_visible=true
            """, (log_id,)
        )
        log = cur.fetchone()
        if not log:
            return
        cur.execute(
            "SELECT * FROM alert_policies WHERE condominium_id=%s AND event_type=%s AND enabled=true ORDER BY channel,recipient_ref",
            (log["condominium_id"], log["event_type"]),
        )
        policies = cur.fetchall()
        for p in policies:
            cur.execute(
                """
                INSERT INTO notification_deliveries(event_log_id,channel,recipient_ref,provider,status,attempts)
                VALUES (%s,%s,%s,%s,'QUEUED',0)
                RETURNING *
                """, (log["id"], p["channel"], p["recipient_ref"], p["provider"])
            )
            delivery = cur.fetchone()
            try:
                if p["channel"] == "WHATSAPP":
                    text = f"INNOVATION VISION IA\n{log['display_name']}\n{log['condominium_name']} · {log['camera_name']}\n{log['occurred_at']}\nConfiança: {log['confidence']}"
                    send_whatsapp(p["provider"], p["recipient_ref"], text)
                    cur.execute("UPDATE notification_deliveries SET status='SENT',attempts=attempts+1,updated_at=now(),last_error=NULL WHERE id=%s", (delivery["id"],))
                else:
                    cur.execute("UPDATE notification_deliveries SET status='FAILED',attempts=attempts+1,updated_at=now(),last_error=%s WHERE id=%s", (f"adapter for {p['channel']} not implemented", delivery["id"]))
            except Exception as exc:
                cur.execute("UPDATE notification_deliveries SET status='FAILED',attempts=attempts+1,updated_at=now(),last_error=%s WHERE id=%s", (str(exc)[:2000], delivery["id"]))
        conn.commit()


def main():
    ensure_group()
    while True:
        rows = r.xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {INPUT_STREAM: ">"}, count=16, block=5000)
        for _, messages in rows:
            for message_id, fields in messages:
                try:
                    log_id = fields.get("event_log_id")
                    if log_id:
                        process(log_id)
                    r.xack(INPUT_STREAM, CONSUMER_GROUP, message_id)
                except Exception as exc:
                    print(json.dumps({"level": "error", "message_id": message_id, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
