from src.event_parser import EventParser
from src.frame_sources import FrameState, _apply_hid_event_to_state


def _encode_ir_basic(points):
    encoded = []
    for idx in range(0, len(points), 2):
        x1, y1 = points[idx]
        x2, y2 = points[idx + 1]
        encoded.extend(
            [
                x1 & 0xFF,
                y1 & 0xFF,
                ((y1 >> 8) & 0x03) << 6 | ((x1 >> 8) & 0x03) << 4 | ((y2 >> 8) & 0x03) << 2 | ((x2 >> 8) & 0x03),
                x2 & 0xFF,
                y2 & 0xFF,
            ]
        )
    return bytes(encoded)


def test_button_press_and_release():
    parser = EventParser()

    press = parser.parse(bytes([0x30, 0x00, 0x08]))  # BTN_A on
    assert len(press) == 1
    assert press[0].kind == "button"
    assert press[0].name == "BTN_A"
    assert press[0].value == 1

    release = parser.parse(bytes([0x30, 0x00, 0x00]))  # BTN_A off
    assert len(release) == 1
    assert release[0].name == "BTN_A"
    assert release[0].value == 0


def test_accel_report_emits_accel_event():
    parser = EventParser()
    events = parser.parse(bytes([0x31, 0x00, 0x00, 120, 128, 140]))

    assert len(events) == 1
    assert events[0].kind == "accel"
    assert events[0].name == "ACCEL"
    assert events[0].value == (120, 128, 140)


def test_multiple_button_changes_in_single_report():
    parser = EventParser()
    events = parser.parse(bytes([0x30, 0x01, 0x08]))  # BTN_LEFT + BTN_A
    names = {event.name: event.value for event in events}

    assert names["BTN_LEFT"] == 1
    assert names["BTN_A"] == 1


def test_report_0x37_emits_accel_ir_and_gyro_events():
    parser = EventParser()
    ir_payload = _encode_ir_basic(
        [
            (512, 384),
            (300, 200),
            (1023, 1023),
            (700, 500),
        ]
    )
    motion_plus_payload = bytes([0x7F, 0x7F, 0x7F, 0x7F, 0x7F, 0x7E])

    events = parser.parse(bytes([0x37, 0x00, 0x00, 120, 128, 140, *ir_payload, *motion_plus_payload]))
    by_kind = {event.kind: event.value for event in events}

    assert by_kind["accel"] == (120, 128, 140)
    assert by_kind["gyro"] == (8063, 8063, 8063)
    assert by_kind["ir"] == (
        (512, 384, None),
        (300, 200, None),
        (None, None, None),
        (700, 500, None),
    )


def test_hid_state_updates_gyro_and_ir_events():
    parser = EventParser()
    state = FrameState.create()
    ir_payload = _encode_ir_basic(
        [
            (450, 320),
            (200, 100),
            (1023, 1023),
            (1023, 1023),
        ]
    )
    motion_plus_payload = bytes([0x12, 0x34, 0x56, 0x7E, 0x7F, 0x7E])

    changed = False
    for event in parser.parse(bytes([0x37, 0x00, 0x00, 1, 2, 3, *ir_payload, *motion_plus_payload])):
        changed = _apply_hid_event_to_state(state, event) or changed

    assert changed is True
    assert state.accel == {"x": 1, "y": 2, "z": 3}
    assert state.gyro == {"x": 7988, "y": 8022, "z": 7954}
    assert state.ir[0] == {"x": 450, "y": 320, "size": None}
    assert state.ir[1] == {"x": 200, "y": 100, "size": None}
