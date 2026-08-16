import json
import os
import time
import urllib.error
import urllib.request

import psycopg
from psycopg.rows import dict_row

from vendor_adapter import IntelbrasWineAdapter, VendorAdapterError

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
STREAM_BROKER_URL = os.getenv("STREAM_BROKER_URL", "http://stream-broker:8091").rstrip("/")
FAILOVER_ORCHESTRATOR_URL = os.getenv("FAILOVER_ORCHESTRATOR_URL", "http://failover-orchestrator:8092").rstrip("/")
INTERVAL_SECONDS = max(5, int(os.getenv("P2P_WATCHDOG_INTERVAL_SECONDS", "15")))
HTTP_TIMEOUT = float(os.getenv("P2P_WATCHDOG_HTTP_TIMEOUT", "30"))
AUTO_FAILOVER_ENABLED = os.getenv("P2P_AUTO_FAILOVER_ENABLED", "true").lower() == "true"

adapter = IntelbrasWineAdapter()


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail[:2000]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"request failed {url}: {exc}") from exc


def primary_sessions():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (s.dvr_id)
                   s.id,s.dvr_id,s.vendor_session_ref,s.state,s.last_frame_at,s.frame_probe_count,
                   w.worker_key,
                   CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END AS is_routed
              FROM p2p_sessions s
              LEFT JOIN wine_workers w ON w.id=s.wine_worker_id
              LEFT JOIN stream_routes r ON r.p2p_session_id=s.id AND r.state='ACTIVE' AND r.deactivated_at IS NULL
             WHERE s.ended_at IS NULL AND s.state IN ('ACTIVE','DEGRADED')
             ORDER BY s.dvr_id,is_routed DESC,s.started_at DESC
            """
        )
        return cur.fetchall()


def record_health(session: dict, healthy: bool, result: dict | None, error: str | None):
    frames = int((result or {}).get("frames_seen", 0) or 0)
    latency = (result or {}).get("latency_ms")
    sdk_error = error or (result or {}).get("last_sdk_error")
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE p2p_sessions
               SET state=%s,last_health_at=now(),
                   last_frame_at=CASE WHEN %s>0 THEN now() ELSE last_frame_at END,
                   frame_probe_count=GREATEST(frame_probe_count,%s),
                   latency_ms=COALESCE(%s,latency_ms),
                   last_sdk_error=%s
             WHERE id=%s AND ended_at IS NULL
            """,
            ("ACTIVE" if healthy else "DEGRADED", frames, frames, latency, sdk_error, session["id"]),
        )
        conn.commit()


def observe_and_maybe_failover(session: dict, healthy: bool, reason: str):
    decision = post(
        f"{STREAM_BROKER_URL}/v1/failover/observe",
        {"dvr_id": str(session["dvr_id"]), "healthy": healthy, "reason": reason[:255]},
    )
    if decision.get("trigger_failover") and AUTO_FAILOVER_ENABLED:
        return post(
            f"{FAILOVER_ORCHESTRATOR_URL}/v1/failover/{session['dvr_id']}/run",
            {"actor_type": "WATCHDOG", "reason": reason[:255], "execute": True},
        )
    return None


def check_session(session: dict):
    if not session.get("vendor_session_ref"):
        record_health(session, False, None, "vendor_session_ref missing")
        observe_and_maybe_failover(session, False, "vendor_session_ref_missing")
        return
    status = adapter.status()
    if not (status.enabled and status.configured and status.executable_found):
        # The control plane may be started for diagnostics with the vendor disabled.
        # Do not mutate historical sessions or trigger failover in that mode.
        return
    try:
        result = adapter.health(session_ref=session["vendor_session_ref"])
        healthy_value = result.get("healthy")
        if not isinstance(healthy_value, bool):
            raise VendorAdapterError("vendor health result must contain boolean healthy")
        frames_seen = int(result.get("frames_seen", 0) or 0)
        healthy = healthy_value and frames_seen >= 0
        reason = str(result.get("reason") or ("vendor_health_ok" if healthy else "vendor_health_failed"))
        record_health(session, healthy, result, None if healthy else reason)
        triggered = observe_and_maybe_failover(session, healthy, reason)
        if triggered:
            print(json.dumps({"level": "warning", "event": "auto_failover", "dvr_id": str(session["dvr_id"]), "result": triggered}), flush=True)
    except Exception as exc:
        reason = f"health_probe_error:{exc}"
        record_health(session, False, None, str(exc)[:2000])
        try:
            triggered = observe_and_maybe_failover(session, False, reason)
            if triggered:
                print(json.dumps({"level": "warning", "event": "auto_failover", "dvr_id": str(session["dvr_id"]), "result": triggered}), flush=True)
        except Exception as nested:
            print(json.dumps({"level": "error", "event": "health_observation_failed", "dvr_id": str(session["dvr_id"]), "error": str(nested)}), flush=True)


def main():
    while True:
        try:
            for session in primary_sessions():
                check_session(session)
        except Exception as exc:
            print(json.dumps({"level": "error", "event": "watchdog_cycle_failed", "error": str(exc)}), flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
