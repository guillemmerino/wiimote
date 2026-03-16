"""Bluetooth utilities backed by bluetoothctl."""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass


MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", re.IGNORECASE)


@dataclass
class BluetoothDevice:
    mac: str
    name: str


def _run_bluetoothctl(args: list[str], timeout: int = 20) -> str:
    cmd = ["bluetoothctl", *args]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bluetoothctl failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _is_benign_scan_off_error(exc: RuntimeError) -> bool:
    text = str(exc)
    return "Failed to stop discovery" in text or "org.bluez.Error.Failed" in text


def _is_already_paired_error(exc: RuntimeError) -> bool:
    text = str(exc)
    return "org.bluez.Error.AlreadyExists" in text or "AlreadyExists" in text


def scan_devices(duration_seconds: int = 8) -> list[BluetoothDevice]:
    _run_bluetoothctl(["scan", "on"])
    try:
        time.sleep(max(2, duration_seconds))
        output = _run_bluetoothctl(["devices"])
    finally:
        try:
            _run_bluetoothctl(["scan", "off"])
        except RuntimeError as exc:
            # BlueZ can intermittently fail to stop discovery even though scan worked.
            if not _is_benign_scan_off_error(exc):
                raise

    devices: list[BluetoothDevice] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Device "):
            continue
        parts = shlex.split(line)
        if len(parts) < 3:
            continue
        mac = parts[1]
        name = " ".join(parts[2:])
        if MAC_RE.fullmatch(mac):
            devices.append(BluetoothDevice(mac=mac.upper(), name=name))
    return devices


def pair_and_connect(mac: str, trust: bool = True) -> None:
    if trust:
        _run_bluetoothctl(["trust", mac])
    try:
        _run_bluetoothctl(["pair", mac], timeout=45)
    except RuntimeError as exc:
        if not _is_already_paired_error(exc):
            raise
    _run_bluetoothctl(["connect", mac], timeout=45)


def connect(mac: str) -> None:
    _run_bluetoothctl(["connect", mac], timeout=45)


def disconnect(mac: str) -> None:
    _run_bluetoothctl(["disconnect", mac], timeout=20)
