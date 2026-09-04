from state_monitor import DogStateMonitor


def bbox_for(center_x: float, center_y: float, width: float, height: float):
    return (
        center_x - width / 2,
        center_y - height / 2,
        center_x + width / 2,
        center_y + height / 2,
    )


def test_dog_static_aspect_change_opens_suspect_zone() -> None:
    monitor = DogStateMonitor()
    boxes = [
        bbox_for(0.50, 0.60, 0.10, 0.25),
        bbox_for(0.50, 0.60, 0.11, 0.24),
        bbox_for(0.50, 0.60, 0.14, 0.22),
        bbox_for(0.50, 0.60, 0.16, 0.20),
    ]
    events = []
    for timestamp, bbox in zip((1000.0, 1002.0, 1004.0, 1005.1), boxes, strict=True):
        events.extend(monitor.observe_dog("cam-1", 7, bbox, 960, 540, timestamp))
    assert len(events) == 1
    assert events[0]["event_key"] == "cachorro_fazendo_fezes"
    assert events[0]["certification_required"] is True


def test_moving_dog_does_not_open_zone() -> None:
    monitor = DogStateMonitor()
    events = []
    for index, timestamp in enumerate((1000.0, 1002.0, 1004.0, 1005.1)):
        bbox = bbox_for(0.15 + index * 0.12, 0.60, 0.10, 0.25)
        events.extend(monitor.observe_dog("cam-1", 8, bbox, 960, 540, timestamp))
    assert events == []


def test_not_collected_after_owner_departure() -> None:
    monitor = DogStateMonitor(no_pickup_seconds=30.0)
    boxes = [
        bbox_for(0.50, 0.60, 0.10, 0.25),
        bbox_for(0.50, 0.60, 0.11, 0.24),
        bbox_for(0.50, 0.60, 0.14, 0.22),
        bbox_for(0.50, 0.60, 0.16, 0.20),
    ]
    for timestamp, bbox in zip((1000.0, 1002.0, 1004.0, 1005.1), boxes, strict=True):
        monitor.observe_dog("cam-1", 9, bbox, 960, 540, timestamp)

    # Associate a person near the zone, then remove them far enough/entirely.
    monitor.update_people("cam-1", [(100, (480.0, 324.0))], 1006.0)
    monitor.update_people("cam-1", [], 1007.0)
    events = monitor.update_people("cam-1", [], 1037.1)
    assert any(event["event_key"] == "morador_nao_recolheu_fezes" for event in events)
