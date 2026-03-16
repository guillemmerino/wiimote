"""CLI entrypoint for Wiimote MVP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import struct
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

try:
    import hid  # type: ignore
except Exception as exc:  # pragma: no cover
    hid = None
    HID_IMPORT_ERROR = exc
else:  # pragma: no cover
    HID_IMPORT_ERROR = None

from .bluetooth_manager import connect, pair_and_connect, scan_devices
from .action_mapper import ActionMapper, extract_ir_pointer
from .event_parser import EventParser, WiimoteEvent
from .wiimote_protocol import build_set_report_mode_payload


NINTENDO_VENDOR_ID = 0x057E
AUTO_PRODUCT_ID = 0x0000
KNOWN_WIIMOTE_PIDS = {0x0306, 0x0330}
DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent.parent / "config" / "mapping.json"
# Linux input_event: type(u16), code(u16), value(s32)
INPUT_EVENT_STRUCT = struct.Struct("llHHi")

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
ABS_X = 0x00
ABS_Y = 0x01
ABS_Z = 0x02
ABS_RX = 0x03
ABS_RY = 0x04
ABS_RZ = 0x05
ABS_HAT0X = 0x10
ABS_HAT0Y = 0x11
ABS_HAT1X = 0x12
ABS_HAT1Y = 0x13
ABS_HAT2X = 0x14
ABS_HAT2Y = 0x15
ABS_HAT3X = 0x16
ABS_HAT3Y = 0x17

WIIMOTE_KEY_MAP = {
    0x101: "TWO",
    0x102: "ONE",
    0x130: "A",
    0x131: "B",
    0x13C: "HOME",
    0x197: "MINUS",
    0x19C: "PLUS",
}
GENERIC_KEY_MAP = {
    103: "UP",
    105: "LEFT",
    106: "RIGHT",
    108: "DOWN",
}
HID_BUTTON_NAME_MAP = {
    "BTN_A": "A",
    "BTN_B": "B",
    "BTN_ONE": "ONE",
    "BTN_TWO": "TWO",
    "BTN_PLUS": "PLUS",
    "BTN_MINUS": "MINUS",
    "BTN_HOME": "HOME",
    "BTN_UP": "UP",
    "BTN_DOWN": "DOWN",
    "BTN_LEFT": "LEFT",
    "BTN_RIGHT": "RIGHT",
}
BUTTON_NAMES = [
    "A",
    "B",
    "ONE",
    "TWO",
    "PLUS",
    "MINUS",
    "HOME",
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
]
SENSOR_AXIS_CODES = {
    ABS_X: "x",
    ABS_Y: "y",
    ABS_Z: "z",
    ABS_RX: "x",
    ABS_RY: "y",
    ABS_RZ: "z",
}
IR_COORD_CODES = {
    ABS_X: (0, "x"),
    ABS_Y: (0, "y"),
    ABS_Z: (1, "x"),
    ABS_RX: (1, "y"),
    ABS_RY: (2, "x"),
    ABS_RZ: (2, "y"),
    ABS_HAT0X: (0, "x"),
    ABS_HAT0Y: (0, "y"),
    ABS_HAT1X: (1, "x"),
    ABS_HAT1Y: (1, "y"),
    ABS_HAT2X: (2, "x"),
    ABS_HAT2Y: (2, "y"),
    ABS_HAT3X: (3, "x"),
    ABS_HAT3Y: (3, "y"),
}


@dataclass(frozen=True)
class InputNodeInfo:
    name: str
    event_path: str


@dataclass
class FrameState:
    buttons: dict[str, int]
    accel: dict[str, int | None]
    gyro: dict[str, int | None]
    ir: list[dict[str, int | None]]

    @classmethod
    def create(cls) -> "FrameState":
        return cls(
            buttons={name: 0 for name in BUTTON_NAMES},
            accel={"x": None, "y": None, "z": None},
            gyro={"x": None, "y": None, "z": None},
            ir=[{"x": None, "y": None, "size": None} for _ in range(4)],
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "buttons": dict(self.buttons),
            "accel": dict(self.accel),
            "gyro": dict(self.gyro),
            "ir": [dict(point) for point in self.ir],
        }


def parse_int(text: str) -> int:
    return int(text, 0)


def parse_path(text: str) -> Path:
    return Path(text).expanduser()


def normalize_mac(text: str) -> str:
    return text.strip().replace("-", ":").upper()


def load_mapping(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise RuntimeError(f"No se pudo leer mapping: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON invalido en mapping: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Mapping debe ser un objeto JSON: {path}")
    return payload


def save_mapping(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
            handle.write("\n")
    except OSError as exc:
        raise RuntimeError(f"No se pudo guardar mapping: {path}: {exc}") from exc


def compute_ir_calibration_bounds(corners: dict[str, tuple[float, float]]) -> dict[str, float]:
    required = ("TL", "TR", "BR", "BL")
    missing = [name for name in required if name not in corners]
    if missing:
        raise RuntimeError(f"Calibracion IR incompleta, faltan esquinas: {', '.join(missing)}")

    tl = corners["TL"]
    tr = corners["TR"]
    br = corners["BR"]
    bl = corners["BL"]

    x_min = (tl[0] + bl[0]) / 2.0
    x_max = (tr[0] + br[0]) / 2.0
    y_min = (tl[1] + tr[1]) / 2.0
    y_max = (bl[1] + br[1]) / 2.0

    if x_max - x_min < 20 or y_max - y_min < 20:
        raise RuntimeError(
            "Calibracion IR invalida (rango demasiado pequeno). "
            "Repite la calibracion apuntando bien a cada esquina."
        )

    return {
        "x_min": round(x_min, 3),
        "x_max": round(x_max, 3),
        "y_min": round(y_min, 3),
        "y_max": round(y_max, 3),
    }


def print_ir_capture_summary(corners: dict[str, tuple[float, float]]) -> None:
    order = ("TL", "TR", "BR", "BL")
    print("Resumen puntos capturados:")
    for key in order:
        point = corners.get(key)
        if point is None:
            print(f"  {key}: (sin capturar)")
            continue
        print(f"  {key}: x={point[0]:.1f}, y={point[1]:.1f}")


def _decode_path(path_value: Any) -> str:
    if isinstance(path_value, bytes):
        return path_value.decode(errors="ignore")
    return str(path_value)


def _is_wiimote_like(dev: dict[str, Any]) -> bool:
    pid = int(dev.get("product_id") or 0)
    if pid in KNOWN_WIIMOTE_PIDS:
        return True

    text = " ".join(
        [
            str(dev.get("manufacturer_string") or ""),
            str(dev.get("product_string") or ""),
            str(dev.get("serial_number") or ""),
            _decode_path(dev.get("path")),
        ]
    ).upper()
    return "RVL-CNT" in text or "WIIMOTE" in text


def _enumerate_hid(vendor_id: int, product_id: int) -> list[dict[str, Any]]:
    # `product_id=0` means "auto": request all products for the vendor.
    devices = hid.enumerate(vendor_id, product_id)
    if devices:
        return devices
    # Some backends are stricter; fallback to full enumerate and filter manually.
    if product_id == AUTO_PRODUCT_ID:
        all_devices = hid.enumerate(0, 0)
        return [d for d in all_devices if int(d.get("vendor_id") or 0) == vendor_id]
    return devices


def _try_set_report_mode(device: "hid.device") -> None:
    payload = build_set_report_mode_payload()
    # Different hidapi backends expect slightly different wire format.
    candidates = [
        bytes(payload),
        bytes([0x00, *payload]),
    ]
    for candidate in candidates:
        try:
            device.write(candidate)
            return
        except OSError:
            continue


def _find_hid_path(mac: str | None, vendor_id: int, product_id: int) -> Any:
    if hid is None:
        raise RuntimeError(f"hidapi no disponible: {HID_IMPORT_ERROR}")

    devices = _enumerate_hid(vendor_id, product_id)
    if product_id == AUTO_PRODUCT_ID:
        devices = [dev for dev in devices if _is_wiimote_like(dev)]
    if not devices:
        raise RuntimeError(
            "No hay dispositivos HID Wiimote visibles. "
            "Verifica el emparejado y que esté conectado. "
            "Prueba: `python -m src.main read --mac <MAC> --product-id 0` "
            "o `--product-id 0x0330`."
        )

    if mac is None:
        return devices[0]["path"]

    wanted = normalize_mac(mac)
    wanted_nosep = wanted.replace(":", "")
    for dev in devices:
        serial = str(dev.get("serial_number") or "").upper()
        serial_nosep = serial.replace(":", "")
        path_text = _decode_path(dev.get("path"))
        if serial == wanted or serial_nosep == wanted_nosep or wanted_nosep in path_text.upper():
            return dev["path"]
    raise RuntimeError(
        f"Wiimote {wanted} no encontrado entre dispositivos HID activos. "
        "Prueba `scan` y `connect` primero."
    )


def _parse_input_nodes() -> list[InputNodeInfo]:
    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        raise RuntimeError(f"No se pudo leer /proc/bus/input/devices: {exc}") from exc

    nodes: list[InputNodeInfo] = []
    blocks = [b for b in content.split("\n\n") if b.strip()]
    for block in blocks:
        name = ""
        handlers: list[str] = []
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("N: Name="):
                start = line.find('"')
                end = line.rfind('"')
                if start != -1 and end > start:
                    name = line[start + 1 : end]
            elif line.startswith("H: Handlers="):
                handlers = line.removeprefix("H: Handlers=").split()
        for handler in handlers:
            if handler.startswith("event"):
                nodes.append(InputNodeInfo(name=name, event_path=f"/dev/input/{handler}"))
    return nodes


def _find_input_event_nodes() -> dict[str, str | None]:
    nodes = _parse_input_nodes()
    out: dict[str, str | None] = {
        "buttons": None,
        "accel": None,
        "ir": None,
        "gyro": None,
    }

    for node in nodes:
        if node.name == "Nintendo Wii Remote":
            out["buttons"] = node.event_path
        elif "Nintendo Wii Remote Accelerometer" in node.name:
            out["accel"] = node.event_path
        elif "Nintendo Wii Remote IR" in node.name:
            out["ir"] = node.event_path
        elif "Nintendo Wii Remote Motion Plus" in node.name:
            out["gyro"] = node.event_path

    return out


def _build_structured_frame(state: FrameState) -> dict[str, Any]:
    payload = state.snapshot()
    payload["ts"] = time.time()
    return payload


def _emit_structured_frame(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def _apply_hid_event_to_state(state: FrameState, event: WiimoteEvent) -> bool:
    if event.kind == "button":
        name = HID_BUTTON_NAME_MAP.get(event.name)
        if name is None:
            return False
        new_state = int(event.value)
        if state.buttons.get(name) != new_state:
            state.buttons[name] = new_state
            return True
        return False
    if event.kind == "accel":
        x, y, z = event.value  # type: ignore[misc]
        changed = False
        if state.accel["x"] != int(x):
            state.accel["x"] = int(x)
            changed = True
        if state.accel["y"] != int(y):
            state.accel["y"] = int(y)
            changed = True
        if state.accel["z"] != int(z):
            state.accel["z"] = int(z)
            changed = True
        return changed
    return False


def _read_input_backend(
    args: argparse.Namespace,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    emit_json: bool = True,
    announce: bool = True,
) -> int:
    paths = _find_input_event_nodes()
    if not any(paths.values()):
        raise RuntimeError(
            "No se encontraron eventos de entrada del Wiimote en /dev/input/event*. "
            "Reconecta el mando y prueba `pair-connect` de nuevo."
        )

    fds: list[int] = []
    labels: dict[int, str] = {}
    try:
        for label, path in paths.items():
            if path:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                fds.append(fd)
                labels[fd] = label

        state = FrameState.create()
        if announce:
            print("Leyendo eventos por backend Linux input (/dev/input). Ctrl+C para salir.")

        while True:
            changed = False
            ready, _, _ = select.select(fds, [], [], max(args.poll_ms, 10) / 1000.0)
            for fd in ready:
                chunk = os.read(fd, INPUT_EVENT_STRUCT.size * 32)
                if not chunk:
                    continue
                offset = 0
                while offset + INPUT_EVENT_STRUCT.size <= len(chunk):
                    _, _, ev_type, code, value = INPUT_EVENT_STRUCT.unpack_from(chunk, offset)
                    offset += INPUT_EVENT_STRUCT.size

                    if ev_type == EV_KEY and labels[fd] == "buttons":
                        if value in (0, 1, 2):
                            name = WIIMOTE_KEY_MAP.get(code) or GENERIC_KEY_MAP.get(code)
                            if name is not None:
                                new_state = 1 if value else 0
                                if state.buttons.get(name) != new_state:
                                    state.buttons[name] = new_state
                                    changed = True
                    elif ev_type == EV_ABS and labels[fd] in ("accel", "gyro"):
                        axis = SENSOR_AXIS_CODES.get(code)
                        if axis is not None:
                            sensor = state.accel if labels[fd] == "accel" else state.gyro
                            if sensor.get(axis) != int(value):
                                sensor[axis] = int(value)
                                changed = True
                    elif ev_type == EV_ABS and labels[fd] == "ir":
                        mapping = IR_COORD_CODES.get(code)
                        if mapping is not None:
                            idx, coord = mapping
                            if idx < len(state.ir) and state.ir[idx].get(coord) != int(value):
                                state.ir[idx][coord] = int(value)
                                changed = True
                    elif ev_type == EV_SYN:
                        continue
            if changed:
                payload = _build_structured_frame(state)
                if emit_json:
                    _emit_structured_frame(payload)
                if on_frame is not None:
                    on_frame(payload)
    except KeyboardInterrupt:
        print("\nFin de lectura.")
        return 0
    except OSError as exc:
        raise RuntimeError(
            "No se pudo leer /dev/input/event*. "
            "Comprueba permisos del usuario sobre el grupo `input`."
        ) from exc
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass

    return 0


def _read_hid_backend(
    args: argparse.Namespace,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    emit_json: bool = True,
    announce: bool = True,
) -> int:
    if hid is None:
        raise RuntimeError(f"hidapi no disponible: {HID_IMPORT_ERROR}")

    path = _find_hid_path(args.mac, args.vendor_id, args.product_id)
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(False)
    _try_set_report_mode(dev)

    parser = EventParser()
    state = FrameState.create()
    if announce:
        print("Leyendo eventos por backend HID. Pulsa Ctrl+C para salir.")
    try:
        while True:
            data = dev.read(64, timeout_ms=args.poll_ms)
            if not data:
                continue
            payload = bytes(data)
            changed = False
            for event in parser.parse(payload):
                changed = _apply_hid_event_to_state(state, event) or changed
            if changed:
                payload = _build_structured_frame(state)
                if emit_json:
                    _emit_structured_frame(payload)
                if on_frame is not None:
                    on_frame(payload)
    except KeyboardInterrupt:
        print("\nFin de lectura.")
    finally:
        dev.close()
    return 0


def _run_read_backend(
    args: argparse.Namespace,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    emit_json: bool = True,
    announce: bool = True,
) -> int:
    if args.backend == "hid":
        return _read_hid_backend(args, on_frame=on_frame, emit_json=emit_json, announce=announce)
    if args.backend == "input":
        return _read_input_backend(args, on_frame=on_frame, emit_json=emit_json, announce=announce)

    hid_error: RuntimeError | None = None
    try:
        return _read_hid_backend(args, on_frame=on_frame, emit_json=emit_json, announce=announce)
    except RuntimeError as exc:
        hid_error = exc

    try:
        return _read_input_backend(args, on_frame=on_frame, emit_json=emit_json, announce=announce)
    except RuntimeError as input_exc:
        raise RuntimeError(
            "No se pudo leer el Wiimote por ningún backend.\n"
            f"HID: {hid_error}\n"
            f"INPUT: {input_exc}"
        ) from input_exc


class _IRCalibrationDone(Exception):
    pass


def cmd_scan(args: argparse.Namespace) -> int:
    devices = scan_devices(duration_seconds=args.seconds)
    if not devices:
        print("No se encontraron dispositivos durante el escaneo.")
        return 0
    for dev in devices:
        print(f"{dev.mac}\t{dev.name}")
    return 0


def cmd_pair_connect(args: argparse.Namespace) -> int:
    pair_and_connect(normalize_mac(args.mac), trust=not args.no_trust)
    print(f"Conectado: {normalize_mac(args.mac)}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    connect(normalize_mac(args.mac))
    print(f"Conectado: {normalize_mac(args.mac)}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    return _run_read_backend(args, emit_json=True, announce=True)


def cmd_calibrate_ir(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping)
    ir_cfg = mapping.setdefault("mouse_from_ir", {})
    if not isinstance(ir_cfg, dict):
        raise RuntimeError("`mouse_from_ir` debe ser un objeto JSON en el mapping.")

    capture_button = str(ir_cfg.get("capture_button", "A")).strip().upper()
    if not capture_button:
        capture_button = "A"
    ir_cfg["capture_button"] = capture_button

    steps = [
        ("TL", "esquina superior izquierda"),
        ("TR", "esquina superior derecha"),
        ("BR", "esquina inferior derecha"),
        ("BL", "esquina inferior izquierda"),
    ]
    captured: dict[str, tuple[float, float]] = {}
    current_idx = 0
    last_button_state = 0

    print("Calibracion IR iniciada.")
    print(f"Paso 1/4: apunta a {steps[0][1]} y pulsa {capture_button}.")

    def on_frame(frame: dict[str, Any]) -> None:
        nonlocal current_idx, last_button_state
        buttons = frame.get("buttons")
        if not isinstance(buttons, dict):
            return
        current_state = 1 if int(buttons.get(capture_button, 0)) else 0
        pressed_edge = current_state == 1 and last_button_state == 0
        last_button_state = current_state
        if not pressed_edge:
            return

        point = extract_ir_pointer(frame.get("ir"))
        if point is None:
            print("No hay puntos IR validos visibles. Reintenta en la misma esquina.", flush=True)
            return

        key, description = steps[current_idx]
        captured[key] = point
        print(f"Capturado {key} ({description}): x={point[0]:.1f}, y={point[1]:.1f}", flush=True)
        current_idx += 1
        if current_idx >= len(steps):
            raise _IRCalibrationDone
        next_key, next_desc = steps[current_idx]
        print(f"Paso {current_idx + 1}/4: apunta a {next_desc} y pulsa {capture_button}.", flush=True)

    try:
        _run_read_backend(args, on_frame=on_frame, emit_json=args.print_frames, announce=False)
    except _IRCalibrationDone:
        pass

    print_ir_capture_summary(captured)

    if len(captured) < 4:
        raise RuntimeError("Calibracion IR cancelada o incompleta.")

    bounds = compute_ir_calibration_bounds(captured)
    ir_cfg["enabled"] = bool(ir_cfg.get("enabled", True))
    ir_cfg["mode"] = str(ir_cfg.get("mode", "ir_priority_freeze"))
    ir_cfg["smoothing_alpha"] = float(ir_cfg.get("smoothing_alpha", 0.25))
    ir_cfg["rel_scale_x"] = float(ir_cfg.get("rel_scale_x", 1600))
    ir_cfg["rel_scale_y"] = float(ir_cfg.get("rel_scale_y", 900))
    ir_cfg["max_delta"] = int(ir_cfg.get("max_delta", 40))
    ir_cfg["recalibrate_button"] = str(ir_cfg.get("recalibrate_button", "HOME")).strip().upper() or "HOME"
    ir_cfg["calibration"] = bounds

    save_mapping(args.mapping, mapping)
    print(
        "Calibracion IR guardada en mapping: "
        f"x_min={bounds['x_min']}, x_max={bounds['x_max']}, y_min={bounds['y_min']}, y_max={bounds['y_max']}"
    )
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping)
    mapper = ActionMapper(mapping)
    keys = mapper.required_key_codes()
    mouse_buttons = mapper.required_mouse_buttons()

    sink = None
    if not args.dry_run:
        from .uinput_device import UInputDevice

        sink = UInputDevice(key_codes=keys, mouse_buttons=mouse_buttons)

    using_ir = mapper.using_ir_mouse()
    if using_ir and not mapper.has_ir_calibration():
        print(
            "Aviso: mouse_from_ir esta activo pero no hay calibracion valida. "
            "Ejecuta `python -m src.main calibrate-ir --backend input`.",
            flush=True,
        )

    calibration_notified = False

    def on_frame(frame: dict[str, Any]) -> None:
        nonlocal calibration_notified
        if not using_ir:
            calibrated, seen, target = mapper.calibration_status()
            if not calibrated and not calibration_notified:
                print(
                    f"Calibrando gyro en reposo... ({seen}/{target})",
                    flush=True,
                )
                calibration_notified = True
            elif calibrated and calibration_notified:
                print("Calibracion gyro completada.", flush=True)
                calibration_notified = False

        actions = mapper.process_frame(frame)
        if not actions:
            return
        if args.dry_run:
            payload = [action.to_dict() for action in actions]
            print(json.dumps({"actions": payload, "ts": frame.get("ts")}, ensure_ascii=True), flush=True)
            return
        sink.emit_actions(actions)
        if args.verbose_actions:
            payload = [action.to_dict() for action in actions]
            print(json.dumps({"actions": payload, "ts": frame.get("ts")}, ensure_ascii=True), flush=True)

    mode = "dry-run" if args.dry_run else "uinput"
    print(f"Control activo ({mode}). Ctrl+C para salir.")
    try:
        return _run_read_backend(args, on_frame=on_frame, emit_json=args.print_frames, announce=False)
    finally:
        if sink is not None:
            sink.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MVP lector de Wiimote por Bluetooth/HID/input")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Escanea dispositivos Bluetooth")
    p_scan.add_argument("--seconds", type=int, default=8, help="Segundos de escaneo")
    p_scan.set_defaults(func=cmd_scan)

    p_pair = sub.add_parser("pair-connect", help="Empareja y conecta el Wiimote")
    p_pair.add_argument("mac", help="MAC del Wiimote, ej: 00:1F:C5:AA:BB:CC")
    p_pair.add_argument("--no-trust", action="store_true", help="No marcar como trusted")
    p_pair.set_defaults(func=cmd_pair_connect)

    p_conn = sub.add_parser("connect", help="Conecta un Wiimote ya emparejado")
    p_conn.add_argument("mac", help="MAC del Wiimote")
    p_conn.set_defaults(func=cmd_connect)

    def add_stream_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--mac", default=None, help="MAC para elegir dispositivo concreto")
        target.add_argument("--vendor-id", type=parse_int, default=NINTENDO_VENDOR_ID, help="VID HID")
        target.add_argument(
            "--product-id",
            type=parse_int,
            default=AUTO_PRODUCT_ID,
            help="PID HID (0 = autodetectar, 0x0306 clásico, 0x0330 TR)",
        )
        target.add_argument("--poll-ms", type=int, default=50, help="Timeout de lectura en ms")
        target.add_argument(
            "--backend",
            choices=["auto", "hid", "input"],
            default="auto",
            help="Backend de lectura (auto intenta HID y luego /dev/input)",
        )

    p_read = sub.add_parser("read", help="Lee eventos del Wiimote")
    add_stream_args(p_read)
    p_read.set_defaults(func=cmd_read)

    p_cal_ir = sub.add_parser("calibrate-ir", help="Calibra IR con 4 esquinas y guarda bounds")
    add_stream_args(p_cal_ir)
    p_cal_ir.add_argument(
        "--mapping",
        type=parse_path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Ruta a mapping JSON (default: {DEFAULT_MAPPING_PATH})",
    )
    p_cal_ir.add_argument(
        "--print-frames",
        action="store_true",
        help="Imprime frames JSON durante la calibracion",
    )
    p_cal_ir.set_defaults(func=cmd_calibrate_ir)

    p_control = sub.add_parser("control", help="Mapea el Wiimote a teclado/raton virtual (uinput)")
    add_stream_args(p_control)
    p_control.add_argument(
        "--mapping",
        type=parse_path,
        default=DEFAULT_MAPPING_PATH,
        help=f"Ruta a mapping JSON (default: {DEFAULT_MAPPING_PATH})",
    )
    p_control.add_argument(
        "--dry-run",
        action="store_true",
        help="No escribe en uinput; solo muestra acciones calculadas",
    )
    p_control.add_argument(
        "--print-frames",
        action="store_true",
        help="Imprime tambien los frames JSON crudos durante control",
    )
    p_control.add_argument(
        "--verbose-actions",
        action="store_true",
        help="Imprime acciones emitidas incluso cuando usa uinput",
    )
    p_control.set_defaults(func=cmd_control)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
