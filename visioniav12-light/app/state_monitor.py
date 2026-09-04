from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import hypot
from time import time
from typing import Deque, Iterable

from geometry import Point, distance


@dataclass(slots=True)
class TrackObservation:
    timestamp: float
    bbox: tuple[float, float, float, float]
    center_px: Point
    aspect_ratio: float


@dataclass(slots=True)
class SuspectZone:
    camera_id: str
    dog_track_id: int
    center_px: Point
    created_at: float
    owner_track_id: int | None = None
    owner_departed_at: float | None = None
    pickup_started_at: float | None = None
    picked_up: bool = False


class DogStateMonitor:
    """Temporal rule reconstructed from the July engine.

    It detects a suspicious defecation posture from dog movement and aspect-ratio
    change. It does not claim that feces are visually proven; downstream
    certification and playback remain mandatory.
    """

    def __init__(
        self,
        history_seconds: float = 6.0,
        minimum_observations: int = 4,
        static_px: float = 28.0,
        static_seconds: float = 5.0,
        aspect_change: float = 0.18,
        owner_radius: float = 260.0,
        collect_radius: float = 90.0,
        departure_radius: float = 180.0,
        pickup_presence_seconds: float = 2.0,
        no_pickup_seconds: float = 30.0,
    ) -> None:
        self.history_seconds = history_seconds
        self.minimum_observations = minimum_observations
        self.static_px = static_px
        self.static_seconds = static_seconds
        self.aspect_change = aspect_change
        self.owner_radius = owner_radius
        self.collect_radius = collect_radius
        self.departure_radius = departure_radius
        self.pickup_presence_seconds = pickup_presence_seconds
        self.no_pickup_seconds = no_pickup_seconds
        self.history: dict[tuple[str, int], Deque[TrackObservation]] = defaultdict(deque)
        self.zones: dict[tuple[str, int], SuspectZone] = {}

    @staticmethod
    def _bbox_metrics(bbox: Iterable[float], frame_width: int, frame_height: int) -> tuple[Point, float]:
        x1, y1, x2, y2 = map(float, bbox)
        width = max((x2 - x1) * frame_width, 1.0)
        height = max((y2 - y1) * frame_height, 1.0)
        center = (((x1 + x2) / 2.0) * frame_width, ((y1 + y2) / 2.0) * frame_height)
        return center, width / height

    def observe_dog(
        self,
        camera_id: str,
        track_id: int,
        bbox: tuple[float, float, float, float],
        frame_width: int,
        frame_height: int,
        timestamp: float | None = None,
    ) -> list[dict]:
        ts = timestamp or time()
        center, aspect = self._bbox_metrics(bbox, frame_width, frame_height)
        key = (camera_id, int(track_id))
        queue = self.history[key]
        queue.append(TrackObservation(ts, bbox, center, aspect))
        while queue and ts - queue[0].timestamp > self.history_seconds:
            queue.popleft()

        if len(queue) < self.minimum_observations:
            return []

        elapsed = queue[-1].timestamp - queue[0].timestamp
        max_displacement = max(distance(queue[0].center_px, obs.center_px) for obs in queue)
        min_aspect = min(obs.aspect_ratio for obs in queue)
        max_aspect = max(obs.aspect_ratio for obs in queue)
        aspect_delta = max_aspect - min_aspect

        if elapsed >= self.static_seconds and max_displacement <= self.static_px and aspect_delta >= self.aspect_change:
            if key not in self.zones:
                zone = SuspectZone(camera_id, int(track_id), center, ts)
                self.zones[key] = zone
                return [{
                    "event_key": "cachorro_fazendo_fezes",
                    "camera_id": camera_id,
                    "track_id": int(track_id),
                    "occurred_at": ts,
                    "confidence": 0.70,
                    "reason": {
                        "history_seconds": elapsed,
                        "observations": len(queue),
                        "max_displacement_px": max_displacement,
                        "aspect_ratio_change": aspect_delta,
                        "zone_center_px": center,
                    },
                    "certification_required": True,
                }]
        return []

    def update_people(
        self,
        camera_id: str,
        people: Iterable[tuple[int, Point]],
        timestamp: float | None = None,
    ) -> list[dict]:
        ts = timestamp or time()
        results: list[dict] = []
        people_list = list(people)

        for key, zone in list(self.zones.items()):
            if zone.camera_id != camera_id or zone.picked_up:
                continue

            nearest: tuple[int, float] | None = None
            for person_track_id, center in people_list:
                dist = distance(zone.center_px, center)
                if nearest is None or dist < nearest[1]:
                    nearest = (person_track_id, dist)

            if nearest and nearest[1] <= self.owner_radius and zone.owner_track_id is None:
                zone.owner_track_id = nearest[0]

            if nearest and nearest[1] <= self.collect_radius:
                if zone.pickup_started_at is None:
                    zone.pickup_started_at = ts
                elif ts - zone.pickup_started_at >= self.pickup_presence_seconds:
                    zone.picked_up = True
                    results.append({
                        "event_key": "possiveis_fezes",
                        "camera_id": camera_id,
                        "occurred_at": ts,
                        "confidence": 0.55,
                        "reason": {"outcome": "possible_pickup", "zone": zone.center_px},
                        "certification_required": True,
                    })
                continue
            zone.pickup_started_at = None

            owner_center = next((center for track, center in people_list if track == zone.owner_track_id), None)
            if zone.owner_track_id is not None:
                if owner_center is None or distance(zone.center_px, owner_center) >= self.departure_radius:
                    if zone.owner_departed_at is None:
                        zone.owner_departed_at = ts
                else:
                    zone.owner_departed_at = None

            if zone.owner_departed_at and ts - zone.owner_departed_at >= self.no_pickup_seconds:
                results.append({
                    "event_key": "morador_nao_recolheu_fezes",
                    "camera_id": camera_id,
                    "occurred_at": ts,
                    "confidence": 0.70,
                    "reason": {
                        "zone": zone.center_px,
                        "owner_track_id": zone.owner_track_id,
                        "seconds_after_departure": ts - zone.owner_departed_at,
                    },
                    "certification_required": True,
                })
                del self.zones[key]

        return results
