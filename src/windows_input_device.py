"""Windows input sink based on SendInput."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import sys

from .action_codes import windows_mouse_flags_from_neutral, windows_vk_from_neutral
from .action_mapper import Action


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]


@dataclass
class WindowsInputDevice:
    _send_input: object

    def __init__(
        self,
        key_codes: set[str],
        mouse_buttons: set[str],
        send_input: object | None = None,
    ) -> None:
        self._validate_supported_codes(key_codes, mouse_buttons)
        if send_input is not None:
            self._send_input = send_input
            return
        if not sys.platform.startswith("win"):
            raise RuntimeError("WindowsInputDevice solo esta disponible en Windows.")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        send_input_fn = user32.SendInput
        send_input_fn.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        send_input_fn.restype = ctypes.c_uint
        self._send_input = send_input_fn

    def close(self) -> None:
        return None

    def emit_actions(self, actions: list[Action]) -> None:
        inputs: list[INPUT] = []
        for action in actions:
            if action.kind == "key":
                inputs.append(self._build_key_input(action.code, bool(action.value)))
            elif action.kind == "mouse_button":
                inputs.append(self._build_mouse_button_input(action.code, bool(action.value)))
            elif action.kind == "mouse_move":
                dx, dy = action.value  # type: ignore[misc]
                if dx or dy:
                    inputs.append(self._build_mouse_move_input(int(dx), int(dy)))
        if not inputs:
            return
        array_type = INPUT * len(inputs)
        result = self._send_input(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
        if result != len(inputs):
            raise RuntimeError("SendInput no pudo inyectar todos los eventos en Windows.")

    def _validate_supported_codes(self, key_codes: set[str], mouse_buttons: set[str]) -> None:
        for code in sorted(key_codes):
            windows_vk_from_neutral(code)
        for code in sorted(mouse_buttons):
            windows_mouse_flags_from_neutral(code, True)

    def _build_key_input(self, code: str, pressed: bool) -> INPUT:
        flags = 0 if pressed else KEYEVENTF_KEYUP
        return INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=windows_vk_from_neutral(code),
                wScan=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=None,
            ),
        )

    def _build_mouse_button_input(self, code: str, pressed: bool) -> INPUT:
        return INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=0,
                dwFlags=windows_mouse_flags_from_neutral(code, pressed),
                time=0,
                dwExtraInfo=None,
            ),
        )

    def _build_mouse_move_input(self, dx: int, dy: int) -> INPUT:
        return INPUT(
            type=INPUT_MOUSE,
            mi=MOUSEINPUT(
                dx=dx,
                dy=dy,
                mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE,
                time=0,
                dwExtraInfo=None,
            ),
        )
