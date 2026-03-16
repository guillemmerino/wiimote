"""Low-level Wiimote protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass


REPORT_BUTTONS = {0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37}
REPORTS_WITH_ACCEL = {0x31, 0x33, 0x35, 0x37}

OUTPUT_REPORT_SET_MODE = 0x12
DEFAULT_MODE_BUTTONS_ACCEL = 0x31

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


def build_set_report_mode_payload(mode: int = DEFAULT_MODE_BUTTONS_ACCEL) -> bytes:
    # 0x00 keeps rumble off.
    return bytes([OUTPUT_REPORT_SET_MODE, 0x00, mode])


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
    return ParsedReport(report_id=report_id, buttons=buttons, accel=accel)

