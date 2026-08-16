from __future__ import annotations

import json
import os
import time
from collections import Counter

import psycopg
from psycopg.rows import dict_row

from t2u_capture import GatewayError, T2UGateway


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
INTERVAL_SECONDS = max(5, int(os.getenv("T2U_STATUS_SYNC_INTERVAL_SECONDS", "10")))


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def sync_once() -> dict[str, int]:
    gateway = T2UGateway()
    bindings = gateway.bindings(include_credentials=False)
    by_name = {item.dvr_name: item for item in bindings}
    connected_by_slug = Counter(
        item.bridge_slug for item in bindings if item.connected and item.local_port
    )

    with db() as conn, conn.cursor() as cur:
        for slug, total in connected_by_slug.items():
            cur.execute(
                """
                INSERT INTO wine_workers(worker_key,host,state,active_sessions,last_heartbeat_at)
                VALUES (%s,'intelbras-t2u-gateway','RUNNING',%s,now())
                ON CONFLICT(worker_key) DO UPDATE
                  SET host=excluded.host,state='RUNNING',active_sessions=excluded.active_sessions,last_heartbeat_at=now()
                """,
                (f"t2u:{slug}", total),
            )

        cur.execute("SELECT id,name FROM dvrs WHERE enabled=true AND connection_mode='intelbras_p2p'")
        dvrs = cur.fetchall()
        synced = 0
        disconnected = 0
        for dvr in dvrs:
            binding = by_name.get(dvr["name"])
            if not binding:
                continue
            vendor_ref = f"t2u:{binding.device_id}"
            cur.execute(
                """
                SELECT id,vendor_metadata FROM p2p_sessions
                 WHERE dvr_id=%s AND vendor_session_ref=%s AND ended_at IS NULL
                 ORDER BY started_at DESC LIMIT 1
                """,
                (dvr["id"], vendor_ref),
            )
            session = cur.fetchone()
            if binding.connected and binding.local_port:
                metadata = json.dumps(
                    {
                        "adapter": "intelbras_t2u_sdk",
                        "bridge": binding.bridge_slug,
                        "device_id": binding.device_id,
                        "status_updated_at": binding.updated_at,
                        "bridge_started_at": binding.bridge_started_at,
                    }
                )
                cur.execute(
                    "SELECT id FROM wine_workers WHERE worker_key=%s",
                    (f"t2u:{binding.bridge_slug}",),
                )
                worker = cur.fetchone()
                restarted = bool(
                    session
                    and isinstance(session.get("vendor_metadata"), dict)
                    and session["vendor_metadata"].get("bridge_started_at") != binding.bridge_started_at
                )
                if restarted:
                    cur.execute(
                        """
                        UPDATE p2p_sessions
                           SET state='CLOSED',ended_at=now(),close_reason='t2u_gateway_restarted',last_health_at=now()
                         WHERE id=%s
                        """,
                        (session["id"],),
                    )
                    session = None
                if session:
                    cur.execute(
                        """
                        UPDATE p2p_sessions
                           SET state='ACTIVE',sdk_local_port=%s,rtsp_local_port=NULL,
                               relay_mode='T2U_SDK',last_health_at=now(),last_sdk_error=NULL,
                               vendor_metadata=%s::jsonb
                         WHERE id=%s
                        """,
                        (binding.local_port, metadata, session["id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO p2p_sessions(
                          dvr_id,wine_worker_id,sdk_local_port,rtsp_local_port,state,open_reason,
                          actor_type,vendor_session_ref,relay_mode,last_health_at,vendor_metadata
                        ) VALUES (%s,%s,%s,NULL,'ACTIVE','t2u_gateway_status','SYSTEM',%s,'T2U_SDK',now(),%s::jsonb)
                        """,
                        (dvr["id"], worker["id"] if worker else None, binding.local_port, vendor_ref, metadata),
                    )
                cur.execute(
                    """
                    UPDATE cameras
                       SET health_state=CASE
                            WHEN last_frame_at >= now()-interval '90 seconds' THEN 'ONLINE'
                            ELSE 'P2P_CONNECTED_NO_VIDEO'
                           END,
                           last_heartbeat_at=now(),updated_at=now()
                     WHERE dvr_id=%s AND enabled=true
                    """,
                    (dvr["id"],),
                )
                synced += 1
            elif session:
                cur.execute(
                    """
                    UPDATE p2p_sessions
                       SET state='CLOSED',ended_at=now(),close_reason='t2u_gateway_disconnected',
                           last_health_at=now()
                     WHERE id=%s
                    """,
                    (session["id"],),
                )
                cur.execute(
                    """
                    UPDATE cameras
                       SET health_state='OFFLINE',last_heartbeat_at=now(),updated_at=now()
                     WHERE dvr_id=%s AND enabled=true AND health_state<>'ONLINE'
                    """,
                    (dvr["id"],),
                )
                disconnected += 1
        conn.commit()
    return {"mapped": len(bindings), "synced": synced, "disconnected": disconnected}


def main() -> None:
    while True:
        try:
            print(json.dumps({"event": "t2u_status_sync", **sync_once()}), flush=True)
        except GatewayError as exc:
            print(json.dumps({"level": "error", "event": "t2u_status_sync", "error": str(exc)}), flush=True)
        except Exception as exc:
            print(json.dumps({"level": "error", "event": "t2u_status_sync", "error": type(exc).__name__}), flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

