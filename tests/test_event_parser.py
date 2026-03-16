from src.event_parser import EventParser


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

