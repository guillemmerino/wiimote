"""Platform-neutral action code normalization and resolution."""

from __future__ import annotations

import re


CANONICAL_KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
}

CANONICAL_MOUSE_ALIASES = {
    "mouse1": "left",
    "mouse2": "right",
    "mouse3": "middle",
}

WINDOWS_VK_BY_KEY = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "leftshift": 0xA0,
    "rightshift": 0xA1,
    "leftctrl": 0xA2,
    "rightctrl": 0xA3,
    "leftalt": 0xA4,
    "rightalt": 0xA5,
}

WINDOWS_MOUSE_EVENT_FLAGS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}


def normalize_key_code(code: str) -> str:
    token = code.strip()
    if not token:
        raise RuntimeError("Codigo de tecla vacio en mapping.")
    if token.lower().startswith("key:"):
        return f"key:{_canonicalize_key_token(token.split(':', 1)[1])}"
    if token.upper().startswith("KEY_"):
        return f"key:{_canonicalize_key_token(token[4:])}"
    return f"key:{_canonicalize_key_token(token)}"


def normalize_mouse_button_code(code: str) -> str:
    token = code.strip()
    if not token:
        raise RuntimeError("Codigo de raton vacio en mapping.")
    if token.lower().startswith("mouse:"):
        return f"mouse:{_canonicalize_mouse_token(token.split(':', 1)[1])}"
    if token.upper().startswith("BTN_"):
        return f"mouse:{_canonicalize_mouse_token(token[4:])}"
    return f"mouse:{_canonicalize_mouse_token(token)}"


def evdev_code_from_neutral(code: str) -> str:
    if code.startswith("key:"):
        return f"KEY_{code.split(':', 1)[1].upper()}"
    if code.startswith("mouse:"):
        button = code.split(':', 1)[1]
        if button not in WINDOWS_MOUSE_EVENT_FLAGS:
            raise RuntimeError(f"Boton de raton no soportado: {code}")
        return f"BTN_{button.upper()}"
    raise RuntimeError(f"Codigo neutral no soportado: {code}")


def windows_vk_from_neutral(code: str) -> int:
    if not code.startswith("key:"):
        raise RuntimeError(f"Codigo de tecla neutral invalido: {code}")
    key = code.split(':', 1)[1]
    if len(key) == 1 and key.isalpha():
        return ord(key.upper())
    if len(key) == 1 and key.isdigit():
        return ord(key)
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    vk = WINDOWS_VK_BY_KEY.get(key)
    if vk is None:
        raise RuntimeError(f"Tecla no soportada en Windows: {code}")
    return vk


def windows_mouse_flags_from_neutral(code: str, pressed: bool) -> int:
    if not code.startswith("mouse:"):
        raise RuntimeError(f"Codigo de raton neutral invalido: {code}")
    button = code.split(':', 1)[1]
    flags = WINDOWS_MOUSE_EVENT_FLAGS.get(button)
    if flags is None:
        raise RuntimeError(f"Boton de raton no soportado en Windows: {code}")
    return flags[0] if pressed else flags[1]


def _canonicalize_key_token(token: str) -> str:
    cleaned = _clean_token(token)
    if not cleaned:
        raise RuntimeError("Codigo de tecla invalido en mapping.")
    return CANONICAL_KEY_ALIASES.get(cleaned, cleaned)


def _canonicalize_mouse_token(token: str) -> str:
    cleaned = _clean_token(token)
    cleaned = CANONICAL_MOUSE_ALIASES.get(cleaned, cleaned)
    if cleaned not in WINDOWS_MOUSE_EVENT_FLAGS:
        raise RuntimeError(f"Boton de raton no soportado en mapping: {token}")
    return cleaned


def _clean_token(token: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", token).lower()
