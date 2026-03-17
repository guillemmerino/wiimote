"""Platform-aware action sink factory."""

from __future__ import annotations

import sys
from typing import Protocol


class ActionSink(Protocol):
    def emit_actions(self, actions: list[object]) -> None:
        ...

    def close(self) -> None:
        ...


def create_action_sink(key_codes: set[str], mouse_buttons: set[str]) -> ActionSink:
    if sys.platform.startswith("win"):
        from .windows_input_device import WindowsInputDevice

        return WindowsInputDevice(key_codes=key_codes, mouse_buttons=mouse_buttons)

    from .uinput_device import UInputDevice

    return UInputDevice(key_codes=key_codes, mouse_buttons=mouse_buttons)
