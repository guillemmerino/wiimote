"""Convert parsed Wiimote reports into high-level events."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .wiimote_protocol import BUTTON_MASKS, parse_report


@dataclass(frozen=True)
class WiimoteEvent:
    timestamp: float
    kind: str
    name: str
    value: int | tuple[int, int, int]


class EventParser:
    def __init__(self) -> None:
        self._last_buttons = 0

    def parse(self, data: bytes) -> list[WiimoteEvent]:
        parsed = parse_report(data)
        if parsed is None:
            return []

        now = time.time()
        events: list[WiimoteEvent] = []

        if parsed.buttons is not None:
            current = parsed.buttons
            changed = self._last_buttons ^ current
            for name, mask in BUTTON_MASKS.items():
                if changed & mask:
                    pressed = 1 if (current & mask) else 0
                    events.append(WiimoteEvent(now, "button", name, pressed))
            self._last_buttons = current

        if parsed.accel is not None:
            events.append(WiimoteEvent(now, "accel", "ACCEL", parsed.accel))

        return events

