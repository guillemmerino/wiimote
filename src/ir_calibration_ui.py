"""Simple Tkinter preview for IR calibration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RAW_IR_MAX_X = 1023.0
RAW_IR_MAX_Y = 767.0
CANVAS_WIDTH = 860
CANVAS_HEIGHT = 520
PADDING = 28


@dataclass
class IRCalibrationPreview:
    _tk: Any
    _root: Any
    _canvas: Any
    _title_var: Any
    _status_var: Any
    _hint_var: Any
    _available: bool

    @classmethod
    def create(cls) -> "IRCalibrationPreview":
        try:
            import tkinter as tk
        except Exception:
            return cls._unavailable()

        try:
            root = tk.Tk()
        except Exception:
            return cls._unavailable()

        root.title("Wiimote IR Calibration")
        root.geometry("920x720")
        root.minsize(820, 620)

        title_var = tk.StringVar(value="IR calibration")
        status_var = tk.StringVar(value="Waiting for IR points...")
        hint_var = tk.StringVar(
            value="Move the Wiimote until the dot follows your aim. Press A to capture each corner."
        )

        title_label = tk.Label(root, textvariable=title_var, font=("Segoe UI", 16, "bold"))
        title_label.pack(pady=(16, 8))

        hint_label = tk.Label(root, textvariable=hint_var, font=("Segoe UI", 10), justify="left")
        hint_label.pack(pady=(0, 8))

        canvas = tk.Canvas(root, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="#101820", highlightthickness=0)
        canvas.pack(padx=16, pady=8, fill="both", expand=True)

        status_label = tk.Label(root, textvariable=status_var, font=("Consolas", 10), justify="left")
        status_label.pack(pady=(8, 16))

        preview = cls(
            _tk=tk,
            _root=root,
            _canvas=canvas,
            _title_var=title_var,
            _status_var=status_var,
            _hint_var=hint_var,
            _available=True,
        )
        preview.set_status("Waiting for IR points...")
        preview.redraw(
            point=None,
            visible_count=0,
            target_key="TL",
            target_desc="esquina superior izquierda",
            captured={},
            invert_x=False,
            invert_y=False,
        )
        return preview

    @classmethod
    def disabled(cls) -> "IRCalibrationPreview":
        return cls._unavailable()

    @classmethod
    def _unavailable(cls) -> "IRCalibrationPreview":
        return cls(_tk=None, _root=None, _canvas=None, _title_var=None, _status_var=None, _hint_var=None, _available=False)

    def is_available(self) -> bool:
        return self._available

    def set_status(self, message: str) -> None:
        if not self._available:
            return
        self._status_var.set(message)
        self._pump()

    def redraw(
        self,
        *,
        point: tuple[float, float] | None,
        visible_count: int,
        target_key: str,
        target_desc: str,
        captured: dict[str, tuple[float, float]],
        invert_x: bool,
        invert_y: bool,
    ) -> None:
        if not self._available:
            return
        if not self._pump():
            return

        self._title_var.set(f"IR calibration: capture {target_key}")
        self._hint_var.set(
            "Target: "
            + target_desc
            + f". Preview aligned to screen coordinates (invert_x={invert_x}, invert_y={invert_y})."
        )

        canvas = self._canvas
        canvas.delete("all")

        left = PADDING
        top = PADDING
        right = max(left + 100, canvas.winfo_width() - PADDING)
        bottom = max(top + 100, canvas.winfo_height() - PADDING)

        canvas.create_rectangle(left, top, right, bottom, outline="#3E6B89", width=2)
        canvas.create_text(left, top - 10, text="Screen-aligned IR preview", fill="#D8E6F0", anchor="sw", font=("Segoe UI", 10, "bold"))

        self._draw_targets(left, top, right, bottom, target_key=target_key, captured=captured, invert_x=invert_x, invert_y=invert_y)
        self._draw_axes(left, top, right, bottom)

        if point is not None:
            px, py = self._project_point(point, left, top, right, bottom, invert_x=invert_x, invert_y=invert_y)
            canvas.create_line(px - 12, py, px + 12, py, fill="#FFD166", width=2)
            canvas.create_line(px, py - 12, px, py + 12, fill="#FFD166", width=2)
            canvas.create_oval(px - 7, py - 7, px + 7, py + 7, fill="#FF5A5F", outline="")
            canvas.create_text(px + 14, py - 14, text=f"({point[0]:.1f}, {point[1]:.1f})", fill="#F7F7F7", anchor="sw")
            self._status_var.set(f"visible={visible_count}  current=({point[0]:.1f}, {point[1]:.1f})")
        else:
            self._status_var.set(f"visible={visible_count}  no valid IR point")

        self._pump()

    def close(self) -> None:
        if not self._available:
            return
        try:
            self._root.destroy()
        except Exception:
            pass
        self._available = False

    def _draw_targets(
        self,
        left: int,
        top: int,
        right: int,
        bottom: int,
        *,
        target_key: str,
        captured: dict[str, tuple[float, float]],
        invert_x: bool,
        invert_y: bool,
    ) -> None:
        markers = {
            "TL": (left, top, "TL"),
            "TR": (right, top, "TR"),
            "BR": (right, bottom, "BR"),
            "BL": (left, bottom, "BL"),
        }

        for key, (x, y, label) in markers.items():
            color = "#FFB703" if key == target_key else "#7BD389"
            radius = 10 if key == target_key else 7
            self._canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline=color, width=2)
            self._canvas.create_text(
                x + (18 if x == left else -18),
                y + (18 if y == top else -18),
                text=label,
                fill=color,
                anchor=("nw" if x == left and y == top else "ne" if x == right and y == top else "se" if x == right else "sw"),
                font=("Consolas", 10, "bold"),
            )

        for key, point in captured.items():
            px, py = self._project_point(point, left, top, right, bottom, invert_x=invert_x, invert_y=invert_y)
            self._canvas.create_oval(px - 5, py - 5, px + 5, py + 5, fill="#7BD389", outline="")
            self._canvas.create_text(px + 8, py + 8, text=key, fill="#7BD389", anchor="nw", font=("Consolas", 9, "bold"))

    def _draw_axes(self, left: int, top: int, right: int, bottom: int) -> None:
        mid_x = (left + right) / 2
        mid_y = (top + bottom) / 2
        self._canvas.create_line(mid_x, top, mid_x, bottom, fill="#27465C", dash=(4, 4))
        self._canvas.create_line(left, mid_y, right, mid_y, fill="#27465C", dash=(4, 4))
        self._canvas.create_text(left, bottom + 12, text="x=0", fill="#B8CCD8", anchor="nw", font=("Consolas", 9))
        self._canvas.create_text(right, bottom + 12, text="x=1023", fill="#B8CCD8", anchor="ne", font=("Consolas", 9))
        self._canvas.create_text(left - 4, top, text="y=0", fill="#B8CCD8", anchor="se", font=("Consolas", 9))
        self._canvas.create_text(left - 4, bottom, text="y=767", fill="#B8CCD8", anchor="ne", font=("Consolas", 9))

    def _project_point(
        self,
        point: tuple[float, float],
        left: int,
        top: int,
        right: int,
        bottom: int,
        *,
        invert_x: bool,
        invert_y: bool,
    ) -> tuple[float, float]:
        x = max(0.0, min(RAW_IR_MAX_X, point[0]))
        y = max(0.0, min(RAW_IR_MAX_Y, point[1]))
        if invert_x:
            x = RAW_IR_MAX_X - x
        if invert_y:
            y = RAW_IR_MAX_Y - y
        px = left + ((right - left) * (x / RAW_IR_MAX_X))
        py = top + ((bottom - top) * (y / RAW_IR_MAX_Y))
        return (px, py)

    def _pump(self) -> bool:
        if not self._available:
            return False
        try:
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._available = False
            return False
        return True
