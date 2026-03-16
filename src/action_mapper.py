"""Map Wiimote frames to high-level input actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Action:
    kind: str
    code: str
    value: int | tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "code": self.code}
        if self.kind == "mouse_move":
            dx, dy = self.value  # type: ignore[misc]
            payload["dx"] = int(dx)
            payload["dy"] = int(dy)
        else:
            payload["value"] = int(self.value)  # type: ignore[arg-type]
        return payload


DEFAULT_MAPPING: dict[str, Any] = {
    "buttons_to_keys": {
        "A": "KEY_SPACE",
        "PLUS": "KEY_ENTER",
        "MINUS": "KEY_BACKSPACE",
    },
    "buttons_to_mouse": {
        "B": "BTN_LEFT",
    },
    "mouse_from_ir": {
        "enabled": True,
        "mode": "ir_priority_freeze",
        "smoothing_alpha": 0.25,
        "rel_scale_x": 1600,
        "rel_scale_y": 900,
        "max_delta": 40,
        "invert_x": False,
        "invert_y": False,
        "calibration": {
            "x_min": None,
            "x_max": None,
            "y_min": None,
            "y_max": None,
        },
        "recalibrate_button": "HOME",
        "capture_button": "A",
    },
    "mouse_from_gyro": {
        "enabled": False,
        "x_axis": "y",
        "y_axis": "x",
        "invert_x": False,
        "invert_y": True,
        "sensitivity": 0.01,
        "deadzone": 250,
        "max_delta": 25,
        "auto_calibrate": True,
        "calibration_frames": 60,
        "rest_threshold": 220,
        "drift_compensation": True,
        "drift_alpha": 0.02,
        "recalibrate_button": "HOME",
    },
}


def extract_ir_pointer(ir_points: Any) -> tuple[float, float] | None:
    if not isinstance(ir_points, list):
        return None

    visible: list[tuple[float, float]] = []
    for point in ir_points:
        if not isinstance(point, dict):
            continue
        x = _as_float(point.get("x"))
        y = _as_float(point.get("y"))
        if x is None or y is None:
            continue
        visible.append((x, y))

    if not visible:
        return None
    if len(visible) == 1:
        return visible[0]
    x = (visible[0][0] + visible[1][0]) / 2.0
    y = (visible[0][1] + visible[1][1]) / 2.0
    return (x, y)


class ActionMapper:
    def __init__(self, mapping: dict[str, Any] | None = None) -> None:
        cfg = mapping or DEFAULT_MAPPING
        self.buttons_to_keys: dict[str, str] = dict(cfg.get("buttons_to_keys") or {})
        self.buttons_to_mouse: dict[str, str] = dict(cfg.get("buttons_to_mouse") or {})
        self.mouse_from_gyro: dict[str, Any] = dict(cfg.get("mouse_from_gyro") or {})
        self.mouse_from_ir: dict[str, Any] = dict(cfg.get("mouse_from_ir") or {})

        self._button_state: dict[str, int] = {}
        self._edge_button_state: dict[str, int] = {}

        self._gyro_offsets: dict[str, float] = {
            "x": float(self.mouse_from_gyro.get("offset_x", 0.0)),
            "y": float(self.mouse_from_gyro.get("offset_y", 0.0)),
            "z": float(self.mouse_from_gyro.get("offset_z", 0.0)),
        }
        self._calibration_target = max(0, int(self.mouse_from_gyro.get("calibration_frames", 60)))
        self._calibration_samples = 0
        self._calibration_sum: dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._is_calibrated = not bool(self.mouse_from_gyro.get("auto_calibrate", True))

        self._ir_filtered_norm: tuple[float, float] | None = None
        self._ir_last_output_norm: tuple[float, float] | None = None

    def required_key_codes(self) -> set[str]:
        return set(self.buttons_to_keys.values())

    def required_mouse_buttons(self) -> set[str]:
        return set(self.buttons_to_mouse.values())

    def using_ir_mouse(self) -> bool:
        return bool(self.mouse_from_ir.get("enabled", False))

    def has_ir_calibration(self) -> bool:
        cal = self._get_ir_calibration()
        if cal is None:
            return False
        return cal["x_max"] > cal["x_min"] and cal["y_max"] > cal["y_min"]

    def process_frame(self, frame: dict[str, Any]) -> list[Action]:
        actions: list[Action] = []
        buttons = frame.get("buttons")
        if isinstance(buttons, dict):
            actions.extend(self._button_actions(buttons))

        if self.using_ir_mouse():
            if isinstance(buttons, dict):
                ir_recal_btn = str(self.mouse_from_ir.get("recalibrate_button", "HOME")).strip().upper()
                if ir_recal_btn and self._button_pressed_edge(buttons, ir_recal_btn):
                    self.reset_ir_runtime()

            dx, dy = self._ir_mouse_delta(frame.get("ir"))
            if dx or dy:
                actions.append(Action(kind="mouse_move", code="REL", value=(dx, dy)))
            return actions

        if isinstance(buttons, dict):
            gyro_recal_btn = str(self.mouse_from_gyro.get("recalibrate_button", "")).strip().upper()
            if gyro_recal_btn and self._button_pressed_edge(buttons, gyro_recal_btn):
                self.start_recalibration()

        gyro = frame.get("gyro")
        if isinstance(gyro, dict):
            dx, dy = self._gyro_mouse_delta(gyro)
            if dx or dy:
                actions.append(Action(kind="mouse_move", code="REL", value=(dx, dy)))
        return actions

    def calibration_status(self) -> tuple[bool, int, int]:
        return (self._is_calibrated, self._calibration_samples, self._calibration_target)

    def start_recalibration(self) -> None:
        if self._calibration_target <= 0:
            self._is_calibrated = True
            return
        self._is_calibrated = False
        self._calibration_samples = 0
        self._calibration_sum = {"x": 0.0, "y": 0.0, "z": 0.0}

    def reset_ir_runtime(self) -> None:
        self._ir_filtered_norm = None
        self._ir_last_output_norm = None

    def _button_pressed_edge(self, buttons: dict[str, Any], button_name: str) -> bool:
        state = 1 if self._as_int(buttons.get(button_name)) else 0
        prev = self._edge_button_state.get(button_name, 0)
        self._edge_button_state[button_name] = state
        return state == 1 and prev == 0

    def _button_actions(self, buttons: dict[str, Any]) -> list[Action]:
        out: list[Action] = []
        for name, raw in buttons.items():
            state = 1 if self._as_int(raw) else 0
            prev = self._button_state.get(name, 0)
            if state == prev:
                continue
            self._button_state[name] = state

            key_code = self.buttons_to_keys.get(name)
            if key_code:
                out.append(Action(kind="key", code=key_code, value=state))

            mouse_code = self.buttons_to_mouse.get(name)
            if mouse_code:
                out.append(Action(kind="mouse_button", code=mouse_code, value=state))
        return out

    def _ir_mouse_delta(self, ir_points: Any) -> tuple[int, int]:
        if not self.using_ir_mouse():
            return (0, 0)
        if not self.has_ir_calibration():
            return (0, 0)

        point = extract_ir_pointer(ir_points)
        mode = str(self.mouse_from_ir.get("mode", "ir_priority_freeze"))
        if point is None:
            if mode == "ir_priority_freeze":
                self.reset_ir_runtime()
            return (0, 0)

        norm = self._normalize_ir_point(point)
        if norm is None:
            return (0, 0)

        alpha = float(self.mouse_from_ir.get("smoothing_alpha", 0.25))
        if alpha <= 0:
            alpha = 1.0
        if alpha > 1.0:
            alpha = 1.0

        if self._ir_filtered_norm is None:
            self._ir_filtered_norm = norm
            self._ir_last_output_norm = norm
            return (0, 0)

        prev_filtered = self._ir_filtered_norm
        filtered = (
            prev_filtered[0] + alpha * (norm[0] - prev_filtered[0]),
            prev_filtered[1] + alpha * (norm[1] - prev_filtered[1]),
        )
        prev_output = self._ir_last_output_norm if self._ir_last_output_norm is not None else prev_filtered
        dx_norm = filtered[0] - prev_output[0]
        dy_norm = filtered[1] - prev_output[1]

        self._ir_filtered_norm = filtered
        self._ir_last_output_norm = filtered

        if bool(self.mouse_from_ir.get("invert_x", False)):
            dx_norm = -dx_norm
        if bool(self.mouse_from_ir.get("invert_y", False)):
            dy_norm = -dy_norm

        scale_x = float(self.mouse_from_ir.get("rel_scale_x", 1600))
        scale_y = float(self.mouse_from_ir.get("rel_scale_y", 900))
        dx = int(round(dx_norm * scale_x))
        dy = int(round(dy_norm * scale_y))

        max_delta = int(self.mouse_from_ir.get("max_delta", 40))
        if max_delta > 0:
            dx = max(-max_delta, min(max_delta, dx))
            dy = max(-max_delta, min(max_delta, dy))
        return (dx, dy)

    def _get_ir_calibration(self) -> dict[str, float] | None:
        calibration = self.mouse_from_ir.get("calibration")
        if not isinstance(calibration, dict):
            return None
        x_min = _as_float(calibration.get("x_min"))
        x_max = _as_float(calibration.get("x_max"))
        y_min = _as_float(calibration.get("y_min"))
        y_max = _as_float(calibration.get("y_max"))
        if x_min is None or x_max is None or y_min is None or y_max is None:
            return None
        return {"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max}

    def _normalize_ir_point(self, point: tuple[float, float]) -> tuple[float, float] | None:
        cal = self._get_ir_calibration()
        if cal is None:
            return None
        span_x = cal["x_max"] - cal["x_min"]
        span_y = cal["y_max"] - cal["y_min"]
        if span_x <= 0.0 or span_y <= 0.0:
            return None
        x = (point[0] - cal["x_min"]) / span_x
        y = (point[1] - cal["y_min"]) / span_y
        return (max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))

    def _gyro_mouse_delta(self, gyro: dict[str, Any]) -> tuple[int, int]:
        cfg = self.mouse_from_gyro
        if not bool(cfg.get("enabled", True)):
            return (0, 0)

        x_axis = str(cfg.get("x_axis", "y"))
        y_axis = str(cfg.get("y_axis", "x"))
        gx_raw = self._as_int(gyro.get(x_axis))
        gy_raw = self._as_int(gyro.get(y_axis))
        if gx_raw is None or gy_raw is None:
            return (0, 0)

        self._update_calibration(gyro)
        if not self._is_calibrated:
            return (0, 0)

        gx = float(gx_raw) - self._gyro_offsets.get(x_axis, 0.0)
        gy = float(gy_raw) - self._gyro_offsets.get(y_axis, 0.0)

        if bool(cfg.get("drift_compensation", True)):
            self._update_drift_offsets(x_axis=x_axis, y_axis=y_axis, gx=gx, gy=gy)
            gx = float(gx_raw) - self._gyro_offsets.get(x_axis, 0.0)
            gy = float(gy_raw) - self._gyro_offsets.get(y_axis, 0.0)

        if bool(cfg.get("invert_x", False)):
            gx = -gx
        if bool(cfg.get("invert_y", True)):
            gy = -gy

        sensitivity = float(cfg.get("sensitivity", 0.01))
        deadzone = int(cfg.get("deadzone", 250))
        max_delta = int(cfg.get("max_delta", 25))

        dx = self._scale_axis(int(gx), sensitivity, deadzone, max_delta)
        dy = self._scale_axis(int(gy), sensitivity, deadzone, max_delta)
        return (dx, dy)

    def _update_calibration(self, gyro: dict[str, Any]) -> None:
        if self._is_calibrated or self._calibration_target <= 0:
            self._is_calibrated = True
            return

        x = self._as_int(gyro.get("x"))
        y = self._as_int(gyro.get("y"))
        z = self._as_int(gyro.get("z"))
        if x is None or y is None or z is None:
            return

        self._calibration_sum["x"] += float(x)
        self._calibration_sum["y"] += float(y)
        self._calibration_sum["z"] += float(z)
        self._calibration_samples += 1
        if self._calibration_samples >= self._calibration_target:
            count = float(self._calibration_samples)
            self._gyro_offsets["x"] = self._calibration_sum["x"] / count
            self._gyro_offsets["y"] = self._calibration_sum["y"] / count
            self._gyro_offsets["z"] = self._calibration_sum["z"] / count
            self._is_calibrated = True

    def _update_drift_offsets(self, x_axis: str, y_axis: str, gx: float, gy: float) -> None:
        cfg = self.mouse_from_gyro
        rest_threshold = float(cfg.get("rest_threshold", 220))
        if abs(gx) > rest_threshold or abs(gy) > rest_threshold:
            return
        alpha = float(cfg.get("drift_alpha", 0.02))
        if alpha <= 0:
            return

        self._gyro_offsets[x_axis] = self._gyro_offsets.get(x_axis, 0.0) + (gx * alpha)
        self._gyro_offsets[y_axis] = self._gyro_offsets.get(y_axis, 0.0) + (gy * alpha)

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _scale_axis(raw: int, sensitivity: float, deadzone: int, max_delta: int) -> int:
        magnitude = abs(raw)
        if magnitude <= deadzone:
            return 0

        signed = -1 if raw < 0 else 1
        scaled = int((magnitude - deadzone) * sensitivity)
        if scaled == 0:
            scaled = 1
        scaled *= signed

        if max_delta > 0:
            if scaled > max_delta:
                return max_delta
            if scaled < -max_delta:
                return -max_delta
        return scaled


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

