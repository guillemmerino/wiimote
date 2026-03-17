import argparse

import pytest

from src.action_codes import normalize_key_code, normalize_mouse_button_code, windows_vk_from_neutral
from src.action_mapper import Action
from src.frame_sources import AUTO_PRODUCT_ID, HIDFrameSource, list_wiimote_hid_devices
from src.main import _create_frame_source, cmd_connect, cmd_pair_connect, cmd_scan
from src.windows_input_device import WindowsInputDevice


def test_legacy_mapping_codes_are_normalized():
    assert normalize_key_code("KEY_SPACE") == "key:space"
    assert normalize_key_code("key:enter") == "key:enter"
    assert normalize_mouse_button_code("BTN_LEFT") == "mouse:left"
    assert normalize_mouse_button_code("mouse:right") == "mouse:right"


def test_windows_vk_resolution_supports_common_keys():
    assert windows_vk_from_neutral("key:space") == 0x20
    assert windows_vk_from_neutral("key:a") == ord("A")
    assert windows_vk_from_neutral("key:1") == ord("1")


def test_windows_input_device_uses_sendinput_for_key_mouse_and_move():
    calls = []

    def fake_send_input(count, inputs, size):
        calls.append((count, size, inputs[0].type, inputs[1].type, inputs[2].type))
        return count

    device = WindowsInputDevice(
        key_codes={"key:space"},
        mouse_buttons={"mouse:left"},
        send_input=fake_send_input,
    )
    device.emit_actions(
        [
            Action(kind="key", code="key:space", value=1),
            Action(kind="mouse_button", code="mouse:left", value=1),
            Action(kind="mouse_move", code="mouse:move", value=(5, -3)),
        ]
    )

    assert len(calls) == 1
    count, _, first_type, second_type, third_type = calls[0]
    assert count == 3
    assert (first_type, second_type, third_type) == (1, 0, 0)


def test_create_frame_source_uses_windows_hid_on_windows(monkeypatch):
    monkeypatch.setattr("src.main.is_windows_platform", lambda: True)
    args = argparse.Namespace(
        backend="auto",
        mac=None,
        vendor_id=0x057E,
        product_id=0,
        poll_ms=50,
        device_path="demo-path",
    )

    source = _create_frame_source(args)

    assert isinstance(source, HIDFrameSource)


def test_windows_bluetooth_commands_fail_clearly(monkeypatch):
    monkeypatch.setattr("src.main.is_windows_platform", lambda: True)
    args = argparse.Namespace(seconds=1, mac="AA:BB:CC:DD:EE:FF", no_trust=False)

    with pytest.raises(RuntimeError, match="no esta soportado en Windows"):
        cmd_scan(args)
    with pytest.raises(RuntimeError, match="no esta soportado en Windows"):
        cmd_pair_connect(args)
    with pytest.raises(RuntimeError, match="no esta soportado en Windows"):
        cmd_connect(args)


def test_list_wiimote_devices_filters_autodetect(monkeypatch):
    class FakeHID:
        @staticmethod
        def enumerate(vendor_id, product_id):
            if vendor_id == 0 and product_id == 0:
                return [
                    {
                        "path": "A",
                        "vendor_id": 0x057E,
                        "product_id": 0x0330,
                        "manufacturer_string": "Nintendo",
                        "product_string": "RVL-CNT-01-TR",
                        "serial_number": "",
                    },
                    {
                        "path": "B",
                        "vendor_id": 0x057E,
                        "product_id": 0x9999,
                        "manufacturer_string": "Nintendo",
                        "product_string": "Something Else",
                        "serial_number": "",
                    },
                ]
            return []

    monkeypatch.setattr("src.frame_sources.hid", FakeHID)

    devices = list_wiimote_hid_devices(vendor_id=0x057E, product_id=AUTO_PRODUCT_ID)

    assert [device.path for device in devices] == ["A"]
