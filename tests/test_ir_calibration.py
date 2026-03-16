import json

from src.main import compute_ir_calibration_bounds, load_mapping, save_mapping


def test_compute_ir_calibration_bounds_from_corners():
    bounds = compute_ir_calibration_bounds(
        {
            "TL": (100.0, 200.0),
            "TR": (900.0, 210.0),
            "BR": (910.0, 700.0),
            "BL": (110.0, 690.0),
        }
    )
    assert bounds["x_min"] == 105.0
    assert bounds["x_max"] == 905.0
    assert bounds["y_min"] == 205.0
    assert bounds["y_max"] == 695.0


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
