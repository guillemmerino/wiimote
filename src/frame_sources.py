"""Platform-aware frame sources for Wiimote data."""

from __future__ import annotations

import os
from dataclasses import dataclass
import json
import select
import struct
import sys
import time
from typing import Any, Callable, Protocol

try:
    import hid  # type: ignore
except Exception as exc:  # pragma: no cover
    hid = None
    HID_IMPORT_ERROR = exc
else:  # pragma: no cover
    HID_IMPORT_ERROR = None

from .event_parser import EventParser, WiimoteEvent
from .wiimote_protocol import (
    build_ir_initialization_sequence,
    build_motion_plus_initialization_sequence,
    build_set_report_mode_payload,
)


FrameCallback = Callable[[dict[str, Any]], None]

NINTENDO_VENDOR_ID = 0x057E
AUTO_PRODUCT_ID = 0x0000
KNOWN_WIIMOTE_PIDS = {0x0306, 0x0330}

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


class FrameSource(Protocol):
    def run(
        self,
        on_frame: FrameCallback | None = None,
        emit_json: bool = True,
        announce: bool = True,
    ) -> int:
        ...


@dataclass(frozen=True)
class InputNodeInfo:
    name: str
    event_path: str


@dataclass(frozen=True)
class HIDDeviceInfo:
    path: str
    vendor_id: int
    product_id: int
    manufacturer: str
    product: str
    serial_number: str


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


class HIDFrameSource:
    def __init__(
        self,
        *,
        mac: str | None,
        vendor_id: int,
        product_id: int,
        poll_ms: int,
        device_path: str | None = None,
        announce_name: str = "HID",
    ) -> None:
        self._mac = mac
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._poll_ms = poll_ms
        self._device_path = device_path
        self._announce_name = announce_name

    def run(
        self,
        on_frame: FrameCallback | None = None,
        emit_json: bool = True,
        announce: bool = True,
    ) -> int:
        if hid is None:
            raise RuntimeError(f"hidapi no disponible: {HID_IMPORT_ERROR}")

        path = _find_hid_path(
            self._mac,
            self._vendor_id,
            self._product_id,
            device_path=self._device_path,
        )
        dev = hid.device()
        dev.open_path(path)
        dev.set_nonblocking(False)
        _try_initialize_hid_features(dev)

        parser = EventParser()
        state = FrameState.create()
        if announce:
            print(f"Leyendo eventos por backend {self._announce_name}. Pulsa Ctrl+C para salir.")
        try:
            while True:
                data = dev.read(64, timeout_ms=self._poll_ms)
                if not data:
                    continue
                payload = bytes(data)
                changed = False
                for event in parser.parse(payload):
                    changed = _apply_hid_event_to_state(state, event) or changed
                if changed:
                    frame = _build_structured_frame(state)
                    if emit_json:
                        _emit_structured_frame(frame)
                    if on_frame is not None:
                        on_frame(frame)
        except KeyboardInterrupt:
            print("\nFin de lectura.")
        finally:
            dev.close()
        return 0


class LinuxInputFrameSource:
    def __init__(self, *, poll_ms: int) -> None:
        self._poll_ms = poll_ms

    def run(
        self,
        on_frame: FrameCallback | None = None,
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
                ready, _, _ = select.select(fds, [], [], max(self._poll_ms, 10) / 1000.0)
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
                    frame = _build_structured_frame(state)
                    if emit_json:
                        _emit_structured_frame(frame)
                    if on_frame is not None:
                        on_frame(frame)
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


class FallbackFrameSource:
    def __init__(self, sources: list[tuple[str, FrameSource]]) -> None:
        self._sources = sources

    def run(
        self,
        on_frame: FrameCallback | None = None,
        emit_json: bool = True,
        announce: bool = True,
    ) -> int:
        failures: list[str] = []
        for label, source in self._sources:
            try:
                return source.run(on_frame=on_frame, emit_json=emit_json, announce=announce)
            except RuntimeError as exc:
                failures.append(f"{label}: {exc}")
        raise RuntimeError("No se pudo leer el Wiimote por ningun backend.\n" + "\n".join(failures))


def is_windows_platform() -> bool:
    return sys.platform.startswith("win")


def list_wiimote_hid_devices(vendor_id: int, product_id: int) -> list[HIDDeviceInfo]:
    if hid is None:
        raise RuntimeError(f"hidapi no disponible: {HID_IMPORT_ERROR}")

    devices = _enumerate_hid(vendor_id, product_id)
    if product_id == AUTO_PRODUCT_ID:
        devices = [dev for dev in devices if _is_wiimote_like(dev)]

    out: list[HIDDeviceInfo] = []
    for device in devices:
        out.append(
            HIDDeviceInfo(
                path=_decode_path(device.get("path")),
                vendor_id=int(device.get("vendor_id") or 0),
                product_id=int(device.get("product_id") or 0),
                manufacturer=str(device.get("manufacturer_string") or ""),
                product=str(device.get("product_string") or ""),
                serial_number=str(device.get("serial_number") or ""),
            )
        )
    return out


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
    devices = hid.enumerate(vendor_id, product_id)
    if devices:
        return devices
    if product_id == AUTO_PRODUCT_ID:
        all_devices = hid.enumerate(0, 0)
        return [d for d in all_devices if int(d.get("vendor_id") or 0) == vendor_id]
    return devices


def _try_set_report_mode(device: "hid.device") -> None:
    payload = build_set_report_mode_payload()
    _try_write_report(device, payload)


def _try_initialize_hid_features(device: "hid.device") -> None:
    for payload in build_motion_plus_initialization_sequence():
        _try_write_report(device, payload)
        time.sleep(0.05)

    for payload in build_ir_initialization_sequence():
        _try_write_report(device, payload)
        time.sleep(0.05)

    _try_set_report_mode(device)


def _try_write_report(device: "hid.device", payload: bytes) -> bool:
    candidates = [
        bytes(payload),
        bytes([0x00, *payload]),
    ]
    for candidate in candidates:
        try:
            device.write(candidate)
            return True
        except OSError:
            continue
    return False


def _find_hid_path(
    mac: str | None,
    vendor_id: int,
    product_id: int,
    *,
    device_path: str | None = None,
) -> Any:
    if hid is None:
        raise RuntimeError(f"hidapi no disponible: {HID_IMPORT_ERROR}")

    devices = _enumerate_hid(vendor_id, product_id)
    if product_id == AUTO_PRODUCT_ID:
        devices = [dev for dev in devices if _is_wiimote_like(dev)]
    if not devices:
        raise RuntimeError(
            "No hay dispositivos HID Wiimote visibles. "
            "Verifica el emparejado o usa `list-devices` para inspeccionar los HID disponibles."
        )

    if device_path is not None:
        wanted_path = device_path.strip()
        for dev in devices:
            if _decode_path(dev.get("path")) == wanted_path:
                return dev["path"]
        raise RuntimeError(f"Device path no encontrado entre los HID activos: {wanted_path}")

    if mac is None:
        return devices[0]["path"]

    wanted = mac.strip().replace("-", ":").upper()
    wanted_nosep = wanted.replace(":", "")
    for dev in devices:
        serial = str(dev.get("serial_number") or "").upper()
        serial_nosep = serial.replace(":", "")
        path_text = _decode_path(dev.get("path"))
        if serial == wanted or serial_nosep == wanted_nosep or wanted_nosep in path_text.upper():
            return dev["path"]
    raise RuntimeError(
        f"Wiimote {wanted} no encontrado entre dispositivos HID activos. "
        "Prueba `list-devices` y revisa el pairing del sistema."
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
    if event.kind == "gyro":
        x, y, z = event.value  # type: ignore[misc]
        changed = False
        if state.gyro["x"] != int(x):
            state.gyro["x"] = int(x)
            changed = True
        if state.gyro["y"] != int(y):
            state.gyro["y"] = int(y)
            changed = True
        if state.gyro["z"] != int(z):
            state.gyro["z"] = int(z)
            changed = True
        return changed
    if event.kind == "ir":
        changed = False
        for idx, point in enumerate(event.value):
            if idx >= len(state.ir):
                break
            x, y, size = point
            if state.ir[idx]["x"] != x:
                state.ir[idx]["x"] = x
                changed = True
            if state.ir[idx]["y"] != y:
                state.ir[idx]["y"] = y
                changed = True
            if state.ir[idx]["size"] != size:
                state.ir[idx]["size"] = size
                changed = True
        return changed
    return False
