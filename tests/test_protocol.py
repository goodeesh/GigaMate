"""Unit tests for the pure-LampArray gigamate.protocol module."""

from unittest.mock import MagicMock, patch

from gigamate.protocol import (
    detect_keyboards,
    get_keyboard,
    hardware_supports_lamparray,
    set_off,
    set_rgb,
    set_static,
)


def test_detect_keyboards_returns_detected_device(tmp_path):
    dev_dir = tmp_path / "hidraw0" / "device"
    dev_dir.mkdir(parents=True)
    (dev_dir / "uevent").write_text("HID_ID=0003:00000414:00008105\nHID_NAME=Gigabyte Aero X16\n")
    with patch("gigamate.protocol.Path", return_value=tmp_path):
        assert detect_keyboards() == [(0x0414, 0x8105)]


def test_detect_keyboards_empty_when_none(tmp_path):
    with patch("gigamate.protocol.Path", return_value=tmp_path):
        assert detect_keyboards() == []


def test_hardware_supports_lamparray_true():
    mock_dev = MagicMock()
    mock_dev.lamp_count.return_value = 96
    with patch("gigamate.lamparray.detect_lamparray_keyboard", return_value=(0x0414, 0x8105, "/dev/hidraw4", 1)), \
         patch("gigamate.protocol.LampArrayDevice", return_value=mock_dev):
        assert hardware_supports_lamparray() is True


def test_hardware_supports_lamparray_false_when_missing():
    with patch("gigamate.lamparray.detect_lamparray_keyboard", return_value=None):
        assert hardware_supports_lamparray() is False


def test_get_keyboard_returns_lamparray_device():
    mock_dev = MagicMock()
    with patch("gigamate.lamparray.detect_lamparray_keyboard", return_value=(0x0414, 0x8105, "/dev/hidraw4", 4)), \
         patch("gigamate.protocol.LampArrayDevice", return_value=mock_dev) as mock_cls:
        assert get_keyboard() is mock_dev
        mock_cls.assert_called_once_with("/dev/hidraw4")


def test_set_rgb_and_set_off_delegate_to_lamparray_device():
    mock_dev = MagicMock()
    mock_dev.set_rgb.return_value = True
    assert set_rgb(mock_dev, "#ff8000", 50) is True
    mock_dev.set_rgb.assert_called_once_with(255, 128, 0, 50)

    mock_dev.set_rgb.reset_mock()
    assert set_off(mock_dev) is True
    mock_dev.set_rgb.assert_called_once_with(0, 0, 0, 0)


def test_set_static_delegates_to_set_rgb():
    mock_dev = MagicMock()
    mock_dev.set_rgb.return_value = True
    assert set_static(mock_dev, "red", brightness_pct=75) is True
    mock_dev.set_rgb.assert_called_once_with(255, 0, 0, 75)

    mock_dev.set_rgb.reset_mock()
    assert set_static(mock_dev, "red", level=0) is True
    mock_dev.set_rgb.assert_called_once_with(0, 0, 0, 0)
