from geometry import DoubleLineState, bottom_center, crossed_line, point_in_polygon


def test_point_in_polygon_positive_and_negative() -> None:
    polygon = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
    assert point_in_polygon((0.5, 0.5), polygon)
    assert not point_in_polygon((0.95, 0.5), polygon)


def test_line_crossing_requires_segment_intersection() -> None:
    line = [(0.5, 0.0), (0.5, 1.0)]
    assert crossed_line((0.2, 0.5), (0.8, 0.5), line)
    assert not crossed_line((0.2, 0.2), (0.3, 0.3), line)


def test_double_line_order_and_timeout() -> None:
    state = DoubleLineState()
    assert not state.observe("L1", 100.0, ("L1", "L2"), 8.0)
    assert state.observe("L2", 105.0, ("L1", "L2"), 8.0)

    assert not state.observe("L1", 200.0, ("L1", "L2"), 8.0)
    assert not state.observe("L2", 210.0, ("L1", "L2"), 8.0)


def test_bottom_center() -> None:
    assert bottom_center((0.2, 0.3, 0.6, 0.9)) == (0.4, 0.9)
