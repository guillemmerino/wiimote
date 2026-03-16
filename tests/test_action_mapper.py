from src.action_mapper import ActionMapper


def _frame(buttons=None, gyro=None, ir=None):
    return {
        "buttons": buttons or {},
        "accel": {"x": None, "y": None, "z": None},
        "gyro": gyro or {"x": None, "y": None, "z": None},
        "ir": ir if ir is not None else [],
        "ts": 0.0,
    }


def test_button_press_and_release_generate_actions():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {"A": "KEY_SPACE"},
            "buttons_to_mouse": {"B": "BTN_LEFT"},
            "mouse_from_ir": {"enabled": False},
            "mouse_from_gyro": {"enabled": False},
        }
    )

    actions = mapper.process_frame(_frame(buttons={"A": 1, "B": 1}))
    names = {(a.kind, a.code, a.value) for a in actions}
    assert ("key", "KEY_SPACE", 1) in names
    assert ("mouse_button", "BTN_LEFT", 1) in names

    actions = mapper.process_frame(_frame(buttons={"A": 0, "B": 0}))
    names = {(a.kind, a.code, a.value) for a in actions}
    assert ("key", "KEY_SPACE", 0) in names
    assert ("mouse_button", "BTN_LEFT", 0) in names


def test_ir_two_points_use_midpoint():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {},
            "buttons_to_mouse": {},
            "mouse_from_ir": {
                "enabled": True,
                "mode": "ir_priority_freeze",
                "smoothing_alpha": 1.0,
                "rel_scale_x": 1000,
                "rel_scale_y": 1000,
                "max_delta": 1000,
                "calibration": {"x_min": 0, "x_max": 1000, "y_min": 0, "y_max": 1000},
            },
            "mouse_from_gyro": {"enabled": False},
        }
    )

    # midpoint (200, 100) then (300, 100): +0.1 in x
    assert mapper.process_frame(_frame(ir=[{"x": 100, "y": 100}, {"x": 300, "y": 100}])) == []
    actions = mapper.process_frame(_frame(ir=[{"x": 200, "y": 100}, {"x": 400, "y": 100}]))
    assert len(actions) == 1
    assert actions[0].kind == "mouse_move"
    dx, dy = actions[0].value
    assert dx == 100
    assert dy == 0


def test_ir_one_point_is_valid():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {},
            "buttons_to_mouse": {},
            "mouse_from_ir": {
                "enabled": True,
                "mode": "ir_priority_freeze",
                "smoothing_alpha": 1.0,
                "rel_scale_x": 500,
                "rel_scale_y": 500,
                "max_delta": 1000,
                "calibration": {"x_min": 0, "x_max": 1000, "y_min": 0, "y_max": 1000},
            },
            "mouse_from_gyro": {"enabled": False},
        }
    )

    assert mapper.process_frame(_frame(ir=[{"x": 100, "y": 100}])) == []
    actions = mapper.process_frame(_frame(ir=[{"x": 300, "y": 100}]))
    assert len(actions) == 1
    dx, dy = actions[0].value
    assert dx == 100
    assert dy == 0


def test_ir_freeze_without_points_and_no_gyro_fallback():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {},
            "buttons_to_mouse": {},
            "mouse_from_ir": {
                "enabled": True,
                "mode": "ir_priority_freeze",
                "smoothing_alpha": 1.0,
                "rel_scale_x": 1000,
                "rel_scale_y": 1000,
                "max_delta": 1000,
                "calibration": {"x_min": 0, "x_max": 1000, "y_min": 0, "y_max": 1000},
            },
            "mouse_from_gyro": {
                "enabled": True,
                "x_axis": "x",
                "y_axis": "y",
                "sensitivity": 1.0,
                "deadzone": 0,
                "max_delta": 1000,
                "auto_calibrate": False,
            },
        }
    )

    actions = mapper.process_frame(_frame(gyro={"x": 20000, "y": 20000, "z": 0}, ir=[]))
    assert actions == []


def test_ir_normalization_clamps_and_respects_max_delta():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {},
            "buttons_to_mouse": {},
            "mouse_from_ir": {
                "enabled": True,
                "mode": "ir_priority_freeze",
                "smoothing_alpha": 1.0,
                "rel_scale_x": 1000,
                "rel_scale_y": 1000,
                "max_delta": 20,
                "calibration": {"x_min": 100, "x_max": 200, "y_min": 100, "y_max": 200},
            },
            "mouse_from_gyro": {"enabled": False},
        }
    )

    assert mapper.process_frame(_frame(ir=[{"x": 100, "y": 100}])) == []
    actions = mapper.process_frame(_frame(ir=[{"x": 350, "y": 350}]))
    assert len(actions) == 1
    dx, dy = actions[0].value
    assert dx == 20
    assert dy == 20


def test_ir_smoothing_reduces_step_response():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {},
            "buttons_to_mouse": {},
            "mouse_from_ir": {
                "enabled": True,
                "mode": "ir_priority_freeze",
                "smoothing_alpha": 0.5,
                "rel_scale_x": 1000,
                "rel_scale_y": 1000,
                "max_delta": 1000,
                "calibration": {"x_min": 0, "x_max": 1000, "y_min": 0, "y_max": 1000},
            },
            "mouse_from_gyro": {"enabled": False},
        }
    )

    assert mapper.process_frame(_frame(ir=[{"x": 100, "y": 100}])) == []
    first = mapper.process_frame(_frame(ir=[{"x": 300, "y": 100}]))
    second = mapper.process_frame(_frame(ir=[{"x": 300, "y": 100}]))
    dx1, _ = first[0].value
    dx2, _ = second[0].value
    assert abs(dx2) < abs(dx1)


def test_gyro_mouse_still_works_when_ir_disabled():
    mapper = ActionMapper(
        {
            "buttons_to_keys": {},
            "buttons_to_mouse": {},
            "mouse_from_ir": {"enabled": False},
            "mouse_from_gyro": {
                "enabled": True,
                "x_axis": "y",
                "y_axis": "x",
                "invert_x": False,
                "invert_y": True,
                "sensitivity": 0.01,
                "deadzone": 200,
                "max_delta": 25,
                "auto_calibrate": False,
            },
        }
    )

    actions = mapper.process_frame(_frame(gyro={"x": 150, "y": 150, "z": 0}))
    assert actions == []

    actions = mapper.process_frame(_frame(gyro={"x": 1000, "y": -1000, "z": 0}))
    assert len(actions) == 1
    dx, dy = actions[0].value
    assert dx < 0
    assert dy < 0
