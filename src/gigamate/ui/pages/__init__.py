"""Pages package for GigaMate Center."""

from .dashboard_page import DashboardPage
from .battery_page import BatteryPage
from .rgb_page import RgbPage
from .automation_page import AutomationPage
from .settings_page import SettingsPage

__all__ = [
    "DashboardPage",
    "BatteryPage",
    "RgbPage",
    "AutomationPage",
    "SettingsPage",
]
