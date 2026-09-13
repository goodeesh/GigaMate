import os
import pytest

# Ensure headless Qt execution in tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PyQt6.QtWidgets import QApplication
except (ImportError, OSError) as exc:
    pytest.skip(f"PyQt6/libEGL not available in this test environment: {exc}", allow_module_level=True)

# Ensure QApplication singleton for test suite
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_creation(qapp):
    from gigamate.ui.main_window import MainWindow

    win = MainWindow()
    assert win.windowTitle() == "GigaMate Center"
    assert win.stack.count() == 4

    # Check navigation switches pages
    win.btn_battery.click()
    assert win.stack.currentIndex() == 1

    win.btn_gpu.click()
    assert win.stack.currentIndex() == 2

    win.btn_rgb.click()
    assert win.stack.currentIndex() == 3

    win.btn_dashboard.click()
    assert win.stack.currentIndex() == 0
