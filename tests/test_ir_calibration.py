import json

from src.main import compute_ir_calibration_bounds, load_mapping, save_mapping


def test_compute_ir_calibration_bounds_from_corners():
    result = compute_ir_calibration_bounds(
        {
            "TL": (100.0, 200.0),
            "TR": (900.0, 210.0),
            "BR": (910.0, 700.0),
            "BL": (110.0, 690.0),
        }
    )
    bounds = result["bounds"]
    assert bounds["x_min"] == 105.0
    assert bounds["x_max"] == 905.0
    assert bounds["y_min"] == 205.0
    assert bounds["y_max"] == 695.0
    assert result["invert_x"] is False
    assert result["invert_y"] is False


def test_compute_ir_calibration_bounds_accepts_mirrored_x_axis():
    result = compute_ir_calibration_bounds(
        {
            "TL": (732.0, 13.0),
            "TR": (261.0, 81.0),
            "BR": (200.5, 403.0),
            "BL": (776.5, 579.5),
        }
    )
    bounds = result["bounds"]

    assert bounds["x_min"] == 230.75
    assert bounds["x_max"] == 754.25
    assert bounds["y_min"] == 47.0
    assert bounds["y_max"] == 491.25
    assert result["invert_x"] is True
    assert result["invert_y"] is False


def test_compute_ir_calibration_bounds_can_trim_edges_for_more_reach():
    result = compute_ir_calibration_bounds(
        {
            "TL": (100.0, 200.0),
            "TR": (900.0, 210.0),
            "BR": (910.0, 700.0),
            "BL": (110.0, 690.0),
        },
        screen_edge_trim=0.10,
    )
    bounds = result["bounds"]

    assert bounds["x_min"] == 185.0
    assert bounds["x_max"] == 825.0
    assert bounds["y_min"] == 254.0
    assert bounds["y_max"] == 646.0
    assert result["screen_edge_trim"] == 0.10


def test_compute_ir_calibration_bounds_allows_more_aggressive_trim():
    result = compute_ir_calibration_bounds(
        {
            "TL": (100.0, 200.0),
            "TR": (900.0, 210.0),
            "BR": (910.0, 700.0),
            "BL": (110.0, 690.0),
        },
        screen_edge_trim=0.30,
    )
    bounds = result["bounds"]

    assert bounds["x_min"] == 345.0
    assert bounds["x_max"] == 665.0
    assert bounds["y_min"] == 352.0
    assert bounds["y_max"] == 548.0
    assert result["screen_edge_trim"] == 0.30


def test_save_and_load_mapping_roundtrip(tmp_path):
    path = tmp_path / "mapping.json"
    payload = {
        "mouse_from_ir": {
            "enabled": True,
            "calibration": {
                "x_min": 1.0,
                "x_max": 2.0,
                "y_min": 3.0,
                "y_max": 4.0,
            },
        }
    }
    save_mapping(path, payload)

    loaded = load_mapping(path)
    assert loaded == payload

    # Ensure file is valid JSON for external tools/users.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == payload
