"""GigaMate Center — Discrete GPU (NVIDIA) sleep-aware tuning page.

Two independent, sleep-aware controls, each with Apply / Reset (stock):

* Undervolt (V/F curve offset) — slider from 0 to +255 MHz.
* Max clock cap — slider from (normal max - 800) MHz up to a little above the
  normal max; the top of the travel is "Stock (unlocked)".

The cap can only *lower* the boost clock: NVML's locked-clock API bounds clocks
within the GPU's already-permitted range, so it cannot raise them. Both controls
apply only while the dGPU is awake and are cleared on suspend / idle / reboot.
"""

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ... import dgpu_tune
from ...config import load as load_config
from ...gpu import get_gpu_state, gpu_status_text


class GpuPage(QWidget):
    """dGPU state + undervolt and max-clock controls (apply only while awake)."""

    _POLL_TICKS = 8  # refresh the live offset roughly every ~12 s (8 * 1.5 s)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tick_count = 0
        self._hw: dict = {}
        self._observed_ceiling = 0
        self._normal_max = dgpu_tune.MAX_CLOCK_MHZ
        self._normal_min = max(300, dgpu_tune.MAX_CLOCK_MHZ - dgpu_tune.MAX_CLOCK_SPAN)
        # Last config values we mirrored into the sliders. Sliders are only
        # repositioned when this changes (or while dragging is not happening),
        # so a background refresh never yanks the handle out from the user.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1500)

    # ── lifecycle ──
    def reload_from_config(self) -> None:
        # Force a fresh mirror of the config into the sliders.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._read_hw()
        self._refresh()

    def _read_hw(self) -> None:
        """Read the live offset + the GPU's max clock (read-only, via helper)."""
        try:
            self._hw = dgpu_tune.get_state() or {}
        except Exception:
            self._hw = {}
        self._recompute_normal_max()

    def _recompute_normal_max(self) -> None:
        """Track the highest reported GPU max clock and add headroom.

        ``nvmlDeviceGetMaxClockInfo`` is power-state dependent (lower when idle),
        so we keep a high-water mark plus headroom; this makes the slider's top
        genuinely mean "unlocked" even under load.
        """
        try:
            probed = dgpu_tune.probe(force=True)
        except Exception:
            probed = {}
        ceiling = probed.get("gpu_max_clock_mhz")
        if ceiling:
            self._observed_ceiling = max(self._observed_ceiling, int(ceiling))
        if self._observed_ceiling <= 0:
            self._observed_ceiling = 3000  # sensible fallback if unreadable
        self._normal_max = self._observed_ceiling + dgpu_tune.MAX_CLOCK_HEADROOM
        self._normal_min = max(300, self._observed_ceiling - dgpu_tune.MAX_CLOCK_SPAN)
        self.max_slider.setRange(self._normal_min, self._normal_max)

    def _on_tick(self) -> None:
        if not self.isVisible():
            return
        self._tick_count += 1
        if self._tick_count % self._POLL_TICKS == 0:
            self._read_hw()
        self._refresh()

    # ── UI ──
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── State card ──
        state_card = QFrame()
        state_card.setProperty("class", "Card")
        sc = QVBoxLayout(state_card)
        sc.setSpacing(8)
        t = QLabel("Discrete GPU")
        t.setProperty("class", "CardTitle")
        sc.addWidget(t)
        self.gpu_state_lbl = QLabel("—")
        self.gpu_state_lbl.setStyleSheet("color: #ffffff; font-size: 18px; font-weight: 700;")
        sc.addWidget(self.gpu_state_lbl)
        self.gpu_holders_lbl = QLabel("")
        self.gpu_holders_lbl.setStyleSheet("color: #8896ab; font-size: 11px;")
        self.gpu_holders_lbl.setWordWrap(True)
        sc.addWidget(self.gpu_holders_lbl)
        layout.addWidget(state_card)

        # ── Undervolt card ──
        uv_card = QFrame()
        uv_card.setProperty("class", "Card")
        uc = QVBoxLayout(uv_card)
        uc.setSpacing(10)

        uv_title = QLabel("Undervolt (V/F curve offset)")
        uv_title.setProperty("class", "CardTitle")
        uc.addWidget(uv_title)
        self.uv_support_lbl = QLabel("")
        self.uv_support_lbl.setStyleSheet("color: #8896ab; font-size: 11px;")
        self.uv_support_lbl.setWordWrap(True)
        uc.addWidget(self.uv_support_lbl)

        uv_row = QHBoxLayout()
        self.uv_slider = QSlider(Qt.Orientation.Horizontal)
        self.uv_slider.setMinimum(0)
        self.uv_slider.setMaximum(dgpu_tune.MAX_OFFSET_MHZ)
        self.uv_slider.setSingleStep(5)
        self.uv_slider.setValue(0)
        self.uv_slider.valueChanged.connect(self._on_uv_slider)
        self.uv_val = QLabel("+0 MHz")
        self.uv_val.setStyleSheet("color: #ffffff; font-weight: 700; min-width: 70px;")
        uv_row.addWidget(self.uv_slider, 1)
        uv_row.addWidget(self.uv_val)
        uc.addLayout(uv_row)

        uv_actions = QHBoxLayout()
        uv_actions.setSpacing(8)
        self.uv_apply_btn = QPushButton("Apply")
        self.uv_apply_btn.setProperty("class", "ProfileButton")
        self.uv_apply_btn.clicked.connect(lambda: self._apply_uv(self.uv_slider.value()))
        self.uv_reset_btn = QPushButton("Reset (stock)")
        self.uv_reset_btn.clicked.connect(lambda: self._apply_uv(0))
        uv_actions.addWidget(self.uv_apply_btn)
        uv_actions.addWidget(self.uv_reset_btn)
        uv_actions.addStretch()
        uc.addLayout(uv_actions)

        self.uv_status_lbl = QLabel("")
        self.uv_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px;")
        uc.addWidget(self.uv_status_lbl)

        uv_note = QLabel(
            "Applied only while the dGPU is awake; cleared automatically when it "
            "suspends and reset to stock on reboot. Higher offset = lower voltage "
            "for a given clock. If the GPU becomes unstable, lower the value."
        )
        uv_note.setStyleSheet("color: #718096; font-size: 11px;")
        uv_note.setWordWrap(True)
        uc.addWidget(uv_note)
        layout.addWidget(uv_card)

        # ── Max clock card ──
        mc_card = QFrame()
        mc_card.setProperty("class", "Card")
        mcc = QVBoxLayout(mc_card)
        mcc.setSpacing(10)

        mc_title = QLabel("Max clock cap")
        mc_title.setProperty("class", "CardTitle")
        mcc.addWidget(mc_title)
        mc_note = QLabel(
            "Upper-bound the boost clock — useful with an aggressive undervolt to "
            "stay at a stable voltage point. The cap can only lower the clock; the "
            "top of the slider is stock (unlocked). Requires the dGPU to be awake."
        )
        mc_note.setStyleSheet("color: #8896ab; font-size: 11px;")
        mc_note.setWordWrap(True)
        mcc.addWidget(mc_note)

        mc_row = QHBoxLayout()
        self.max_slider = QSlider(Qt.Orientation.Horizontal)
        self.max_slider.setRange(self._normal_min, self._normal_max)
        self.max_slider.setSingleStep(25)
        self.max_slider.setValue(self._normal_max)
        self.max_slider.valueChanged.connect(self._on_max_slider)
        self.max_val = QLabel("Stock (unlocked)")
        self.max_val.setStyleSheet("color: #ffffff; font-weight: 700; min-width: 150px;")
        mc_row.addWidget(self.max_slider, 1)
        mc_row.addWidget(self.max_val)
        mcc.addLayout(mc_row)

        mc_actions = QHBoxLayout()
        mc_actions.setSpacing(8)
        self.max_apply_btn = QPushButton("Apply")
        self.max_apply_btn.setProperty("class", "ProfileButton")
        self.max_apply_btn.clicked.connect(lambda: self._apply_max(self.max_slider.value()))
        self.max_reset_btn = QPushButton("Reset (stock)")
        self.max_reset_btn.clicked.connect(lambda: self._apply_max(self._normal_max))
        mc_actions.addWidget(self.max_apply_btn)
        mc_actions.addWidget(self.max_reset_btn)
        mc_actions.addStretch()
        mcc.addLayout(mc_actions)

        self.max_status_lbl = QLabel("")
        self.max_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px;")
        mcc.addWidget(self.max_status_lbl)
        layout.addWidget(mc_card)
        layout.addStretch()

    # ── helpers ──
    def _on_uv_slider(self, value: int) -> None:
        self.uv_val.setText(f"+{value} MHz")

    def _on_max_slider(self, value: int) -> None:
        self._update_max_readout(value)

    def _update_max_readout(self, value: int) -> None:
        if value >= self._observed_ceiling:
            self.max_val.setText("Stock (unlocked)")
        else:
            self.max_val.setText(f"Cap {value} MHz (−{self._observed_ceiling - value})")

    def _apply_uv(self, value: int) -> None:
        try:
            value = max(0, min(dgpu_tune.MAX_OFFSET_MHZ, int(value)))
            dgpu_tune.set_desired_config(enabled=value > 0, offset=value)
        except Exception:
            pass
        self.uv_slider.blockSignals(True)
        self.uv_slider.setValue(value)
        self.uv_slider.blockSignals(False)
        self.uv_val.setText(f"+{value} MHz")
        self._read_hw()
        self._refresh()

    def _apply_max(self, value: int) -> None:
        try:
            value = max(self.max_slider.minimum(),
                        min(self.max_slider.maximum(), int(value)))
            if value >= self._observed_ceiling:
                dgpu_tune.set_desired_config(max_enabled=False, max_clock=0)
            else:
                dgpu_tune.set_desired_config(max_enabled=True, max_clock=value)
        except Exception:
            pass
        self._read_hw()
        self._refresh()

    def _refresh(self) -> None:
        gpu = get_gpu_state()
        if not gpu.present:
            self.gpu_state_lbl.setText("Not present (iGPU only)")
            self.gpu_holders_lbl.setText("")
        else:
            self.gpu_state_lbl.setText(gpu_status_text(gpu))
            holders = []
            try:
                holders = dgpu_tune.wake_holders()
            except Exception:
                pass
            if gpu.status == "active" and holders:
                self.gpu_holders_lbl.setText("Awake held by: " + ", ".join(holders))
            else:
                self.gpu_holders_lbl.setText("")

        supported = False
        probed = {}
        try:
            probed = dgpu_tune.probe()
            supported = bool(probed.get("supported"))
        except Exception:
            pass

        if gpu.vendor != "nvidia":
            self.uv_support_lbl.setText("Tuning is only available for NVIDIA dGPUs.")
            supported = False
        elif supported:
            self.uv_support_lbl.setText(f"Supported on {probed.get('device') or 'NVIDIA GPU'}.")
        else:
            self.uv_support_lbl.setText(
                "Not supported by this GPU/driver: " + str(probed.get("error") or "unavailable")
            )

        self.uv_slider.setEnabled(supported)
        self.uv_apply_btn.setEnabled(supported)
        self.uv_reset_btn.setEnabled(supported)
        self.max_slider.setEnabled(supported)
        self.max_apply_btn.setEnabled(supported)
        self.max_reset_btn.setEnabled(supported)

        if supported:
            self._sync_uv_controls()
            self._sync_max_controls()
        else:
            self.uv_status_lbl.setText("")
            self.max_status_lbl.setText("")

    def _sync_uv_controls(self) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_undervolt_enabled", False))
        offset = int(cfg.get("dgpu_undervolt_offset_mhz", 0) or 0)
        hw_off = self._hw.get("offset_mhz")
        pos = offset if enabled else 0
        # Only mirror config into the slider when it changed, and never while the
        # user is dragging (otherwise the periodic refresh yanks the handle).
        if not self.uv_slider.isSliderDown() and (enabled, pos) != self._uv_cfg_key:
            self.uv_slider.blockSignals(True)
            self.uv_slider.setValue(pos)
            self.uv_slider.blockSignals(False)
            self.uv_val.setText(f"+{pos} MHz")
            self._uv_cfg_key = (enabled, pos)

        if enabled and offset > 0 and hw_off == offset:
            state = f"Applied: +{offset} MHz"
        elif enabled and offset > 0:
            state = "Configured — not applied yet (dGPU asleep or pending)"
        elif hw_off not in (None, 0):
            state = f"Stock requested; hardware still at +{hw_off} MHz"
        else:
            state = "Off (stock)"
        self.uv_status_lbl.setText(state)

    def _sync_max_controls(self) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_max_clock_enabled", False))
        wanted = int(cfg.get("dgpu_max_clock_mhz", 0) or 0)
        applied_max = dgpu_tune.read_state_file().get("applied_max") or 0

        if enabled and wanted > 0:
            # Widen the low end if a smaller cap was set out-of-band (e.g. CLI).
            eff_min = max(300, min(self._normal_min, wanted))
            eff_max = self._normal_max
            pos = max(eff_min, min(eff_max, wanted))
        else:
            eff_min = self._normal_min
            eff_max = self._normal_max
            pos = self._normal_max
        # Keep the range current, but only move the handle when the config
        # actually changed and the user isn't dragging it right now.
        if not self.max_slider.isSliderDown():
            self.max_slider.setRange(eff_min, eff_max)
            if (enabled, wanted) != self._max_cfg_key:
                self.max_slider.blockSignals(True)
                self.max_slider.setValue(pos)
                self.max_slider.blockSignals(False)
                self._max_cfg_key = (enabled, wanted)
            self._update_max_readout(self.max_slider.value())

        if enabled and wanted > 0 and applied_max == wanted:
            state = f"Applied: cap {wanted} MHz"
        elif enabled and wanted > 0:
            state = "Configured — not applied yet (dGPU asleep or pending)"
        elif applied_max:
            state = f"Off requested; hardware still capped at {applied_max} MHz"
        else:
            state = "Off (unlocked)"
        self.max_status_lbl.setText(state)
