from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Iterable, Sequence

Point = tuple[float, float]


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        crosses = (yi > y) != (yj > y)
        if crosses:
            x_intersection = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_intersection:
                inside = not inside
        j = i
    return inside


def orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)
    return (o1 == 0 or o2 == 0 or (o1 > 0) != (o2 > 0)) and (
        o3 == 0 or o4 == 0 or (o3 > 0) != (o4 > 0)
    )


def crossed_line(previous: Point, current: Point, line: Sequence[Point]) -> bool:
    return len(line) == 2 and segments_intersect(previous, current, line[0], line[1])


def line_side(point: Point, line: Sequence[Point]) -> int:
    if len(line) != 2:
        return 0
    value = orientation(line[0], line[1], point)
    if abs(value) < 1e-9:
        return 0
    return 1 if value > 0 else -1


def bottom_center(xyxy: Sequence[float]) -> Point:
    x1, y1, x2, y2 = map(float, xyxy)
    return ((x1 + x2) / 2.0, y2)


def distance(a: Point, b: Point) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


@dataclass(slots=True)
class DoubleLineState:
    first_label: str | None = None
    first_seen_at: float | None = None

    def reset(self) -> None:
        self.first_label = None
        self.first_seen_at = None

    def observe(
        self,
        crossed_label: str,
        now: float,
        expected_sequence: tuple[str, str],
        timeout_seconds: float,
    ) -> bool:
        first, second = expected_sequence
        if self.first_label is not None and self.first_seen_at is not None:
            if now - self.first_seen_at > timeout_seconds:
                self.reset()
        if self.first_label is None:
            if crossed_label == first:
                self.first_label = first
                self.first_seen_at = now
            return False
        if self.first_label == first and crossed_label == second:
            self.reset()
            return True
        if crossed_label == first:
            self.first_seen_at = now
        return False


def normalize_points(points: Iterable[Sequence[float]]) -> list[Point]:
    normalized: list[Point] = []
    for value in points:
        if len(value) != 2:
            raise ValueError("each geometry point must have exactly two coordinates")
        x, y = float(value[0]), float(value[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("geometry coordinates must be normalized to 0..1")
        normalized.append((x, y))
    return normalized
