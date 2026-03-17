"""Low-level Wiimote protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


REPORT_BUTTONS = {0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37}
REPORTS_WITH_ACCEL = {0x31, 0x33, 0x35, 0x37}
REPORTS_WITH_IR_EXTENDED = {0x33}
REPORTS_WITH_IR_BASIC = {0x36, 0x37}
REPORTS_WITH_EXTENSION = {0x32, 0x34, 0x35, 0x36, 0x37}

OUTPUT_REPORT_SET_MODE = 0x12
OUTPUT_REPORT_IR_ENABLE = 0x13
OUTPUT_REPORT_WRITE_MEMORY = 0x16
OUTPUT_REPORT_IR_ENABLE_2 = 0x1A

DEFAULT_MODE_BUTTONS_ACCEL = 0x31
DEFAULT_MODE_BUTTONS_ACCEL_IR_EXTENSION = 0x37

REGISTER_FLAG: Final[int] = 0x04
MOTION_PLUS_INIT_REGISTER: Final[int] = 0xA600F0
MOTION_PLUS_ENABLE_REGISTER: Final[int] = 0xA600FE
MOTION_PLUS_ENABLE_DATA: Final[bytes] = bytes([0x04])
IR_MODE_BASIC: Final[int] = 0x01
IR_SENSITIVITY_BLOCK_1: Final[bytes] = bytes.fromhex("02 00 00 71 01 00 AA 00 64")
IR_SENSITIVITY_BLOCK_2: Final[bytes] = bytes.fromhex("63 03")
IR_INIT_SEQUENCE: Final[tuple[tuple[int, bytes], ...]] = (
    (0xB00030, bytes([0x08])),
    (0xB00000, IR_SENSITIVITY_BLOCK_1),
    (0xB0001A, IR_SENSITIVITY_BLOCK_2),
    (0xB00033, bytes([IR_MODE_BASIC])),
    (0xB00030, bytes([0x08])),
)

BUTTON_MASKS = {
    "BTN_TWO": 0x0001,
    "BTN_ONE": 0x0002,
    "BTN_B": 0x0004,
    "BTN_A": 0x0008,
    "BTN_MINUS": 0x0010,
    "BTN_HOME": 0x0080,
    "BTN_LEFT": 0x0100,
    "BTN_RIGHT": 0x0200,
    "BTN_DOWN": 0x0400,
    "BTN_UP": 0x0800,
    "BTN_PLUS": 0x1000,
}


@dataclass(frozen=True)
class ParsedReport:
    report_id: int
    buttons: int | None = None
    accel: tuple[int, int, int] | None = None
    ir: tuple[tuple[int | None, int | None, int | None], ...] | None = None
    motion_plus: tuple[int, int, int] | None = None


def build_ir_enable_payload(report_id: int, enable_mask: int = REGISTER_FLAG) -> bytes:
    return bytes([report_id, enable_mask])


def build_set_report_mode_payload(mode: int = DEFAULT_MODE_BUTTONS_ACCEL_IR_EXTENSION) -> bytes:
    # 0x00 keeps rumble off.
    return bytes([OUTPUT_REPORT_SET_MODE, 0x00, mode])


def build_write_register_payload(address: int, data: bytes) -> bytes:
    if len(data) > 16:
        raise ValueError("Wiimote register writes admiten un maximo de 16 bytes.")
    return bytes(
        [
            OUTPUT_REPORT_WRITE_MEMORY,
            REGISTER_FLAG,
            (address >> 16) & 0xFF,
            (address >> 8) & 0xFF,
            address & 0xFF,
            len(data) & 0xFF,
            *data,
            *([0x00] * (16 - len(data))),
        ]
    )


def build_motion_plus_initialization_sequence() -> list[bytes]:
    return [
        build_write_register_payload(MOTION_PLUS_INIT_REGISTER, bytes([0x55])),
        build_write_register_payload(MOTION_PLUS_ENABLE_REGISTER, MOTION_PLUS_ENABLE_DATA),
    ]


def build_ir_initialization_sequence() -> list[bytes]:
    sequence = [
        build_ir_enable_payload(OUTPUT_REPORT_IR_ENABLE),
        build_ir_enable_payload(OUTPUT_REPORT_IR_ENABLE_2),
    ]
    for address, data in IR_INIT_SEQUENCE:
        sequence.append(build_write_register_payload(address, data))
    return sequence


def parse_report(data: bytes) -> ParsedReport | None:
    if not data:
        return None
    report_id = data[0]
    if report_id not in REPORT_BUTTONS or len(data) < 3:
        return ParsedReport(report_id=report_id)

    buttons = (data[1] << 8) | data[2]
    accel = None
    if report_id in REPORTS_WITH_ACCEL and len(data) >= 6:
        accel = (data[3], data[4], data[5])

    ir = None
    if report_id in REPORTS_WITH_IR_EXTENDED and len(data) >= 18:
        ir = _parse_ir_extended(data[6:18])
    elif report_id in REPORTS_WITH_IR_BASIC:
        ir_offset = 3 if report_id == 0x36 else 6
        if len(data) >= ir_offset + 10:
            ir = _parse_ir_basic(data[ir_offset : ir_offset + 10])

    motion_plus = None
    if report_id in REPORTS_WITH_EXTENSION:
        extension_offset = {
            0x32: 3,
            0x34: 3,
            0x35: 6,
            0x36: 13,
            0x37: 16,
        }.get(report_id)
        if extension_offset is not None and len(data) >= extension_offset + 6:
            motion_plus = _parse_motion_plus(data[extension_offset : extension_offset + 6])

    return ParsedReport(
        report_id=report_id,
        buttons=buttons,
        accel=accel,
        ir=ir,
        motion_plus=motion_plus,
    )


def _parse_ir_basic(payload: bytes) -> tuple[tuple[int | None, int | None, int | None], ...]:
    if len(payload) != 10:
        return tuple()
    return (
        *_parse_ir_basic_pair(payload[:5]),
        *_parse_ir_basic_pair(payload[5:10]),
    )


def _parse_ir_basic_pair(payload: bytes) -> tuple[tuple[int | None, int | None, int | None], ...]:
    b0, b1, b2, b3, b4 = payload
    point_1 = _normalize_ir_point(
        x=b0 | (((b2 >> 4) & 0x03) << 8),
        y=b1 | (((b2 >> 6) & 0x03) << 8),
        size=None,
    )
    point_2 = _normalize_ir_point(
        x=b3 | ((b2 & 0x03) << 8),
        y=b4 | (((b2 >> 2) & 0x03) << 8),
        size=None,
    )
    return (point_1, point_2)


def _parse_ir_extended(payload: bytes) -> tuple[tuple[int | None, int | None, int | None], ...]:
    if len(payload) != 12:
        return tuple()

    points: list[tuple[int | None, int | None, int | None]] = []
    for offset in range(0, len(payload), 3):
        b0, b1, b2 = payload[offset : offset + 3]
        points.append(
            _normalize_ir_point(
                x=b0 | (((b2 >> 4) & 0x03) << 8),
                y=b1 | (((b2 >> 6) & 0x03) << 8),
                size=b2 & 0x0F,
            )
        )
    return tuple(points)


def _normalize_ir_point(x: int, y: int, size: int | None) -> tuple[int | None, int | None, int | None]:
    if x == 0x3FF and y == 0x3FF:
        return (None, None, None)
    if x < 0 or x > 1023 or y < 0 or y > 767:
        return (None, None, None)
    return (x, y, size)


def _parse_motion_plus(payload: bytes) -> tuple[int, int, int] | None:
    if len(payload) != 6:
        return None
    if (payload[5] & 0x02) == 0 or (payload[5] & 0x01) != 0:
        return None

    yaw = payload[0] | ((payload[3] & 0xFC) << 6)
    roll = payload[1] | ((payload[4] & 0xFC) << 6)
    pitch = payload[2] | ((payload[5] & 0xFC) << 6)
    # Expose roll/pitch/yaw as x/y/z to match the rest of the project.
    return (roll, pitch, yaw)
