"""uinput adapter backed by python-evdev."""

from __future__ import annotations

from dataclasses import dataclass

from .action_mapper import Action


@dataclass
class _Capabilities:
    key_codes: list[int]


class UInputDevice:
    def __init__(self, key_codes: set[str], mouse_buttons: set[str], name: str = "Wiimote Virtual Input"):
        try:
            from evdev import UInput, ecodes  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "No se pudo importar `evdev`. Instala dependencias y verifica permisos de /dev/uinput."
            ) from exc

        self._ecodes = ecodes
        self._uinput_cls = UInput
        cap = self._build_capabilities(key_codes=key_codes, mouse_buttons=mouse_buttons)
        events = {
            ecodes.EV_KEY: cap.key_codes,
            ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
        }
        try:
            self._ui = self._uinput_cls(events=events, name=name, bustype=0x03)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "No se pudo abrir /dev/uinput para escritura. "
                "Ajusta permisos/udev o ejecuta con privilegios."
            ) from exc

    def close(self) -> None:
        self._ui.close()

    def emit_actions(self, actions: list[Action]) -> None:
        dirty = False
        for action in actions:
            if action.kind == "key":
                code = self._resolve_code(action.code)
                self._ui.write(self._ecodes.EV_KEY, code, int(action.value))
                dirty = True
            elif action.kind == "mouse_button":
                code = self._resolve_code(action.code)
                self._ui.write(self._ecodes.EV_KEY, code, int(action.value))
                dirty = True
            elif action.kind == "mouse_move":
                dx, dy = action.value  # type: ignore[misc]
                if dx:
                    self._ui.write(self._ecodes.EV_REL, self._ecodes.REL_X, int(dx))
                    dirty = True
                if dy:
                    self._ui.write(self._ecodes.EV_REL, self._ecodes.REL_Y, int(dy))
                    dirty = True

        if dirty:
            self._ui.syn()

    def _build_capabilities(self, key_codes: set[str], mouse_buttons: set[str]) -> _Capabilities:
        combined = set(key_codes) | set(mouse_buttons) | {"BTN_LEFT", "BTN_RIGHT"}
        resolved = [self._resolve_code(name) for name in sorted(combined)]
        return _Capabilities(key_codes=resolved)

    def _resolve_code(self, name: str) -> int:
        value = self._ecodes.ecodes.get(name)
        if isinstance(value, int):
            return value
        raise RuntimeError(f"Codigo de input desconocido en mapping: {name}")
