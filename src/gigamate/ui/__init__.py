"""GigaMate Center — Modern Desktop Control Panel."""

__all__ = ["run_gui"]


def __getattr__(name):
    # Lazily import the Qt GUI so that ``import gigamate.ui`` works on systems
    # without PyQt6 (headless CI, tray-only installs). Only attribute access
    # pays the Qt import cost.
    if name == "run_gui":
        from .main_window import run_gui

        return run_gui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
