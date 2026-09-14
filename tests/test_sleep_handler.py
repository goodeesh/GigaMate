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
         patch("gigamate.sleep_handler.resolve_active_profile"), \
         patch("gigamate.sleep_handler.load_config", return_value={"startup_apply": True}):
        mock_dev = MagicMock()
        mock_get_kb.return_value = mock_dev

        handler.on_prepare_for_sleep(going_to_sleep=True)

        assert suspend_called is True
        mock_set_off.assert_called_once()


def test_sleep_handler_suspend_respects_opt_out():
    """If the user did not opt into startup control, sleep must not touch RGB."""
    handler = SleepHandler()

    with patch("gigamate.sleep_handler.get_keyboard") as mock_get_kb, \
         patch("gigamate.sleep_handler.set_off") as mock_set_off, \
         patch("gigamate.sleep_handler.resolve_active_profile"), \
         patch("gigamate.sleep_handler.load_config", return_value={"startup_apply": False}):
        mock_get_kb.return_value = MagicMock()

        handler.on_prepare_for_sleep(going_to_sleep=True)

        mock_get_kb.assert_not_called()
        mock_set_off.assert_not_called()


def test_sleep_handler_resume_applies_settings_once():
    resume_called = False

    def on_resume():
        nonlocal resume_called
        resume_called = True

    handler = SleepHandler(on_resume_hook=on_resume)

    with patch("gigamate.sleep_handler.time.sleep"), \
         patch("gigamate.sleep_handler.apply_hardware_settings") as mock_apply:
        handler.on_prepare_for_sleep(going_to_sleep=False)

        assert resume_called is True
        mock_apply.assert_called_once()


def test_sleep_handler_stop_listening_is_idempotent():
    handler = SleepHandler()

    # Never started: stopping must be a safe no-op.
    handler.stop_listening()
    assert handler._listening is False
    assert handler._listener_thread is None
    assert handler._loop is None


def test_sleep_handler_start_stop_listening(monkeypatch):
    handler = SleepHandler()
    # Avoid opening a real system-bus connection in tests.
    monkeypatch.setattr(handler, "_run_dbus_listener", lambda: None)

    if not handler.start_listening():
        pytest.skip("No D-Bus/Gio backend available in this environment")

    assert handler._listening is True
    handler.stop_listening()
    assert handler._listening is False
    assert handler._listener_thread is None


def test_dispatch_hook_runs_directly_on_main_thread():
    called = []
    handler = SleepHandler()
    handler._dispatch_hook(lambda: called.append(1))
    assert called == [1]


def test_dispatch_hook_ignores_none():
    handler = SleepHandler()
    handler._dispatch_hook(None)  # must not raise
