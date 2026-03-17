"""CLI entrypoint for Wiimote MVP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable

from .action_sinks import create_action_sink
from .action_mapper import ActionMapper, extract_ir_pointer
from .bluetooth_manager import connect, pair_and_connect, scan_devices
from .frame_sources import (
    AUTO_PRODUCT_ID,
    NINTENDO_VENDOR_ID,
    FallbackFrameSource,
    HIDFrameSource,
    LinuxInputFrameSource,
    is_windows_platform,
    list_wiimote_hid_devices,
)
from .ir_calibration_ui import IRCalibrationPreview


DEFAULT_MAPPING_PATH = Path(__file__).resolve().parent.parent / "config" / "mapping.json"


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


def compute_ir_calibration_bounds(
    corners: dict[str, tuple[float, float]],
    *,
    screen_edge_trim: float = 0.0,
) -> dict[str, Any]:
    required = ("TL", "TR", "BR", "BL")
    missing = [name for name in required if name not in corners]
    if missing:
        raise RuntimeError(f"Calibracion IR incompleta, faltan esquinas: {', '.join(missing)}")

    tl = corners["TL"]
    tr = corners["TR"]
    br = corners["BR"]
    bl = corners["BL"]

    left_x = (tl[0] + bl[0]) / 2.0
    right_x = (tr[0] + br[0]) / 2.0
    top_y = (tl[1] + tr[1]) / 2.0
    bottom_y = (bl[1] + br[1]) / 2.0

    invert_x = left_x > right_x
    invert_y = top_y > bottom_y

    x_min = min(left_x, right_x)
    x_max = max(left_x, right_x)
    y_min = min(top_y, bottom_y)
    y_max = max(top_y, bottom_y)

    trim = max(0.0, min(0.35, float(screen_edge_trim)))
    if trim:
        span_x = x_max - x_min
        span_y = y_max - y_min
        x_min += span_x * trim
        x_max -= span_x * trim
        y_min += span_y * trim
        y_max -= span_y * trim

    if x_max - x_min < 20 or y_max - y_min < 20:
        raise RuntimeError(
            "Calibracion IR invalida (rango demasiado pequeno). "
            f"x_span={x_max - x_min:.1f}, y_span={y_max - y_min:.1f}. "
            "Repite la calibracion apuntando bien a cada esquina."
        )

    return {
        "bounds": {
            "x_min": round(x_min, 3),
            "x_max": round(x_max, 3),
            "y_min": round(y_min, 3),
            "y_max": round(y_max, 3),
        },
        "invert_x": invert_x,
        "invert_y": invert_y,
        "screen_edge_trim": trim,
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


def count_visible_ir_points(ir_points: Any) -> int:
    if not isinstance(ir_points, list):
        return 0
    count = 0
    for point in ir_points:
        if not isinstance(point, dict):
            continue
        if point.get("x") is None or point.get("y") is None:
            continue
        count += 1
    return count


def format_ir_preview(point: tuple[float, float] | None, visible_count: int, target_key: str) -> str:
    if point is None:
        return f"Vista IR [{target_key}] visible={visible_count} sin punto valido. Pulsa A cuando veas el puntero."

    x_bar = _axis_preview_bar(point[0], axis_max=1023.0, width=24)
    y_bar = _axis_preview_bar(point[1], axis_max=767.0, width=18)
    return (
        f"Vista IR [{target_key}] visible={visible_count} "
        f"x={point[0]:6.1f} {x_bar} "
        f"y={point[1]:6.1f} {y_bar} "
        "Pulsa A para capturar."
    )


def _axis_preview_bar(value: float, axis_max: float, width: int) -> str:
    if width <= 1:
        return "[]"
    clamped = max(0.0, min(axis_max, value))
    ratio = 0.0 if axis_max <= 0 else clamped / axis_max
    idx = min(width - 1, max(0, int(round(ratio * (width - 1)))))
    chars = ["-"] * width
    chars[idx] = "*"
    return "[" + "".join(chars) + "]"


def _create_frame_source(args: argparse.Namespace) -> HIDFrameSource | LinuxInputFrameSource | FallbackFrameSource:
    if is_windows_platform():
        if args.backend == "input":
            raise RuntimeError("El backend `input` no esta soportado en Windows.")
        return HIDFrameSource(
            mac=args.mac,
            vendor_id=args.vendor_id,
            product_id=args.product_id,
            poll_ms=args.poll_ms,
            device_path=args.device_path,
            announce_name="Windows HID",
        )

    hid_source = HIDFrameSource(
        mac=args.mac,
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        poll_ms=args.poll_ms,
        device_path=args.device_path,
        announce_name="HID",
    )
    input_source = LinuxInputFrameSource(poll_ms=args.poll_ms)

    if args.backend == "windows-hid":
        raise RuntimeError("El backend `windows-hid` solo esta soportado en Windows.")
    if args.backend == "hid":
        return hid_source
    if args.backend == "input":
        return input_source
    return FallbackFrameSource(
        [
            ("HID", hid_source),
            ("INPUT", input_source),
        ]
    )


def _run_read_backend(
    args: argparse.Namespace,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    emit_json: bool = True,
    announce: bool = True,
) -> int:
    return _create_frame_source(args).run(on_frame=on_frame, emit_json=emit_json, announce=announce)


def _ensure_windows_bluetooth_not_supported(command_name: str) -> None:
    if is_windows_platform():
        raise RuntimeError(
            f"`{command_name}` no esta soportado en Windows. "
            "Empareja el Wiimote desde la configuracion Bluetooth del sistema y usa `list-devices`."
        )


class _IRCalibrationDone(Exception):
    pass


def cmd_scan(args: argparse.Namespace) -> int:
    _ensure_windows_bluetooth_not_supported("scan")
    devices = scan_devices(duration_seconds=args.seconds)
    if not devices:
        print("No se encontraron dispositivos durante el escaneo.")
        return 0
    for dev in devices:
        print(f"{dev.mac}\t{dev.name}")
    return 0


def cmd_pair_connect(args: argparse.Namespace) -> int:
    _ensure_windows_bluetooth_not_supported("pair-connect")
    pair_and_connect(normalize_mac(args.mac), trust=not args.no_trust)
    print(f"Conectado: {normalize_mac(args.mac)}")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    _ensure_windows_bluetooth_not_supported("connect")
    connect(normalize_mac(args.mac))
    print(f"Conectado: {normalize_mac(args.mac)}")
    return 0


def cmd_list_devices(args: argparse.Namespace) -> int:
    devices = list_wiimote_hid_devices(vendor_id=args.vendor_id, product_id=args.product_id)
    if not devices:
        print("No se encontraron dispositivos HID Wiimote compatibles.")
        return 0
    for dev in devices:
        print(
            f"{dev.path}\tVID=0x{dev.vendor_id:04X}\tPID=0x{dev.product_id:04X}\t"
            f"{dev.manufacturer}\t{dev.product}\t{dev.serial_number}"
        )
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    return _run_read_backend(args, emit_json=True, announce=True)


def cmd_calibrate_ir(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping)
    ir_cfg = mapping.setdefault("mouse_from_ir", {})
    if not isinstance(ir_cfg, dict):
        raise RuntimeError("`mouse_from_ir` debe ser un objeto JSON en el mapping.")

    capture_button = str(ir_cfg.get("capture_button", "A")).strip().upper() or "A"
    ir_cfg["capture_button"] = capture_button
    screen_edge_trim = float(ir_cfg.get("screen_edge_trim", 0.28))
    ir_cfg["screen_edge_trim"] = screen_edge_trim

    steps = [
        ("TL", "esquina superior izquierda"),
        ("TR", "esquina superior derecha"),
        ("BR", "esquina inferior derecha"),
        ("BL", "esquina inferior izquierda"),
    ]
    captured: dict[str, tuple[float, float]] = {}
    current_idx = 0
    last_button_state = 0
    last_preview_line = ""
    last_preview_ts = 0.0
    preview = IRCalibrationPreview.create() if args.gui else IRCalibrationPreview.disabled()

    print("Calibracion IR iniciada.")
    print(f"Paso 1/4: apunta a {steps[0][1]} y pulsa {capture_button}.")
    if preview.is_available():
        preview.set_status(f"Target TL. Press {capture_button} to capture.")
    else:
        print("Vista grafica no disponible. Se mantiene la vista previa por consola.")

    def on_frame(frame: dict[str, Any]) -> None:
        nonlocal current_idx, last_button_state, last_preview_line, last_preview_ts
        buttons = frame.get("buttons")
        if not isinstance(buttons, dict):
            return

        point = extract_ir_pointer(frame.get("ir"))
        visible_count = count_visible_ir_points(frame.get("ir"))
        target_key = steps[current_idx][0]
        target_desc = steps[current_idx][1]
        preview_invert_x = bool(ir_cfg.get("invert_x", False))
        preview_invert_y = bool(ir_cfg.get("invert_y", False))
        if preview.is_available():
            preview.redraw(
                point=point,
                visible_count=visible_count,
                target_key=target_key,
                target_desc=target_desc,
                captured=captured,
                invert_x=preview_invert_x,
                invert_y=preview_invert_y,
            )
        else:
            preview_line = format_ir_preview(point, visible_count, target_key)
            now = time.monotonic()
            if preview_line != last_preview_line or (now - last_preview_ts) >= 0.15:
                print(f"\r{preview_line}", end="", flush=True)
                last_preview_line = preview_line
                last_preview_ts = now

        current_state = 1 if int(buttons.get(capture_button, 0)) else 0
        pressed_edge = current_state == 1 and last_button_state == 0
        last_button_state = current_state
        if not pressed_edge:
            return

        if not preview.is_available():
            print()
        if point is None:
            preview.set_status("No valid IR point visible. Try again in the same corner.")
            print("No hay puntos IR validos visibles. Reintenta en la misma esquina.", flush=True)
            return

        key, description = steps[current_idx]
        captured[key] = point
        preview.set_status(f"Captured {key} at ({point[0]:.1f}, {point[1]:.1f})")
        print(f"Capturado {key} ({description}): x={point[0]:.1f}, y={point[1]:.1f}", flush=True)
        current_idx += 1
        if current_idx >= len(steps):
            raise _IRCalibrationDone
        next_key, next_desc = steps[current_idx]
        preview.set_status(f"Target {next_key}. Press {capture_button} to capture.")
        print(f"Paso {current_idx + 1}/4: apunta a {next_desc} y pulsa {capture_button}.", flush=True)

    try:
        _run_read_backend(args, on_frame=on_frame, emit_json=args.print_frames, announce=False)
    except _IRCalibrationDone:
        pass
    finally:
        preview.close()

    print_ir_capture_summary(captured)

    if len(captured) < 4:
        raise RuntimeError("Calibracion IR cancelada o incompleta.")

    calibration_result = compute_ir_calibration_bounds(captured, screen_edge_trim=screen_edge_trim)
    bounds = calibration_result["bounds"]
    ir_cfg["enabled"] = bool(ir_cfg.get("enabled", True))
    ir_cfg["mode"] = str(ir_cfg.get("mode", "ir_priority_freeze"))
    ir_cfg["smoothing_alpha"] = float(ir_cfg.get("smoothing_alpha", 0.25))
    ir_cfg["rel_scale_x"] = float(ir_cfg.get("rel_scale_x", 1600))
    ir_cfg["rel_scale_y"] = float(ir_cfg.get("rel_scale_y", 900))
    ir_cfg["max_delta"] = int(ir_cfg.get("max_delta", 40))
    ir_cfg["invert_x"] = bool(calibration_result["invert_x"])
    ir_cfg["invert_y"] = bool(calibration_result["invert_y"])
    ir_cfg["recalibrate_button"] = str(ir_cfg.get("recalibrate_button", "HOME")).strip().upper() or "HOME"
    ir_cfg["calibration"] = bounds

    save_mapping(args.mapping, mapping)
    print(
        "Calibracion IR guardada en mapping: "
        f"x_min={bounds['x_min']}, x_max={bounds['x_max']}, "
        f"y_min={bounds['y_min']}, y_max={bounds['y_max']}, "
        f"invert_x={ir_cfg['invert_x']}, invert_y={ir_cfg['invert_y']}, "
        f"screen_edge_trim={ir_cfg['screen_edge_trim']}"
    )
    return 0


def cmd_control(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping)
    mapper = ActionMapper(mapping)
    keys = mapper.required_key_codes()
    mouse_buttons = mapper.required_mouse_buttons()

    sink = None
    if not args.dry_run:
        sink = create_action_sink(key_codes=keys, mouse_buttons=mouse_buttons)

    using_ir = mapper.using_ir_mouse()
    if using_ir and not mapper.has_ir_calibration():
        print(
            "Aviso: mouse_from_ir esta activo pero no hay calibracion valida. "
            "Ejecuta `python -m src.main calibrate-ir` para guardar bounds.",
            flush=True,
        )

    calibration_notified = False

    def on_frame(frame: dict[str, Any]) -> None:
        nonlocal calibration_notified
        if not using_ir:
            calibrated, seen, target = mapper.calibration_status()
            if not calibrated and not calibration_notified:
                print(f"Calibrando {mapper.active_mouse_source()} en reposo... ({seen}/{target})", flush=True)
                calibration_notified = True
            elif calibrated and calibration_notified:
                print("Calibracion completada.", flush=True)
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

    mode = "dry-run" if args.dry_run else "system-input"
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

    p_list = sub.add_parser("list-devices", help="Lista dispositivos HID Wiimote compatibles")
    p_list.add_argument("--vendor-id", type=parse_int, default=NINTENDO_VENDOR_ID, help="VID HID")
    p_list.add_argument(
        "--product-id",
        type=parse_int,
        default=AUTO_PRODUCT_ID,
        help="PID HID (0 = autodetectar, 0x0306 clasico, 0x0330 TR)",
    )
    p_list.set_defaults(func=cmd_list_devices)

    def add_stream_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--mac", default=None, help="MAC para elegir dispositivo concreto")
        target.add_argument("--device-path", default=None, help="Device path HID concreto, util para Windows")
        target.add_argument("--vendor-id", type=parse_int, default=NINTENDO_VENDOR_ID, help="VID HID")
        target.add_argument(
            "--product-id",
            type=parse_int,
            default=AUTO_PRODUCT_ID,
            help="PID HID (0 = autodetectar, 0x0306 clasico, 0x0330 TR)",
        )
        target.add_argument("--poll-ms", type=int, default=50, help="Timeout de lectura en ms")
        target.add_argument(
            "--backend",
            choices=["auto", "hid", "input", "windows-hid"],
            default="auto",
            help="Backend de lectura (auto elige por plataforma; en Windows usa HID)",
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
    p_cal_ir.add_argument(
        "--no-gui",
        dest="gui",
        action="store_false",
        help="Desactiva la vista grafica y usa solo la vista previa por consola",
    )
    p_cal_ir.set_defaults(gui=True)
    p_cal_ir.set_defaults(func=cmd_calibrate_ir)

    p_control = sub.add_parser("control", help="Mapea el Wiimote a teclado/raton virtual")
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
        help="No escribe en el sistema; solo muestra acciones calculadas",
    )
    p_control.add_argument(
        "--print-frames",
        action="store_true",
        help="Imprime tambien los frames JSON crudos durante control",
    )
    p_control.add_argument(
        "--verbose-actions",
        action="store_true",
        help="Imprime acciones emitidas incluso cuando inyecta eventos",
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
