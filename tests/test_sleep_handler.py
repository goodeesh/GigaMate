from unittest.mock import MagicMock, patch
import pytest

from gigamate.sleep_handler import SleepHandler


def test_sleep_handler_suspend():
    suspend_called = False

    def on_suspend():
        nonlocal suspend_called
        suspend_called = True

    handler = SleepHandler(on_suspend_hook=on_suspend)

    with patch("gigamate.sleep_handler.get_keyboard") as mock_get_kb, \
         patch("gigamate.sleep_handler.set_off") as mock_set_off, \
         patch("gigamate.sleep_handler.resolve_active_profile"):
        mock_dev = MagicMock()
        mock_get_kb.return_value = mock_dev

        handler.on_prepare_for_sleep(going_to_sleep=True)

        assert suspend_called is True
        mock_set_off.assert_called_once()


def test_sleep_handler_resume():
    resume_called = False

    def on_resume():
        nonlocal resume_called
        resume_called = True

    handler = SleepHandler(on_resume_hook=on_resume)

    with patch("gigamate.sleep_handler.time.sleep"), \
         patch("gigamate.sleep_handler.get_keyboard") as mock_get_kb, \
         patch("gigamate.sleep_handler.set_static") as mock_set_static, \
         patch("gigamate.sleep_handler.resolve_active_profile"), \
         patch("gigamate.sleep_handler.sync_gpu_power") as mock_sync_gpu, \
         patch("gigamate.sleep_handler.load_config", return_value={"brightness": 2, "colour": "red", "acpi_profile": 3}):

        mock_dev = MagicMock()
        mock_get_kb.return_value = mock_dev

        handler.on_prepare_for_sleep(going_to_sleep=False)

        assert resume_called is True
        mock_set_static.assert_called_once()
        mock_sync_gpu.assert_called_with(3)
