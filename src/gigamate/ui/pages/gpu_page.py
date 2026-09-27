"""GigaMate Center — Discrete GPU (NVIDIA) sleep-aware tuning.

The GPU tuning interface is organized into two primary cards:

* **Core Clock Tuning (Undervolt & Curve Flattening)**:
  - **V/F curve offset**: slider from 0 to +255 MHz (shifts the entire curve up).
  - **Max boost clock cap**: slider to cap the upper boost limit ("flatten above N MHz")
    preventing instability when running an aggressive undervolt.

* **Memory Clock (VRAM Overclock)**:
  - **Memory V/F offset**: slider from -1000 MHz to +2000 MHz (independent memory
    frequency tuning to increase VRAM bandwidth).

A single **Apply** button at the bottom commits all controls together in one go (one config
write, one helper call). Moving a slider only stages a value — nothing reaches
the hardware until Apply. Reset buttons beside each control take effect
immediately to return that specific control to stock.

This page shows what is *configured and applied*, never live hardware telemetry:
temperature, clocks, power and VRAM are MangoHud's and nvtop's job, and every
NVML read risks resuming a dGPU the kernel has powered down. The applied values
come from the state file the watcher service publishes, so a refresh is a file
read and can never wake the GPU; NVML is touched only for a probe, and only
while the dGPU is already awake.
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


class _SliderNoWheel(QSlider):
    """A slider that never eats the page's scroll wheel.

    These sliders now live in a scrollable page. Qt's default behaviour is to
    change the value on any wheel event over the widget, so scrolling the page
    would silently retune the GPU clocks. Ignoring the event hands it to the
    scroll area, which is what the user actually asked for.
    """

    def wheelEvent(self, event):  # noqa: N802 (Qt naming)
        event.ignore()


class GpuPage(QWidget):
    """dGPU undervolt, max-clock and memory-clock controls (apply only awake)."""

    _POLL_TICKS = 8  # refresh the applied state roughly every ~12 s (8 * 1.5 s)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tick_count = 0
        self._hw: dict = {}
        self._observed_ceiling = 0
        self._normal_max = dgpu_tune.MAX_CLOCK_MHZ
        self._normal_min = max(300, dgpu_tune.MAX_CLOCK_MHZ - dgpu_tune.MAX_CLOCK_SPAN)
        # Config values currently mirrored into the widgets. Sliders are only
        # repositioned when this changes (and never mid-drag), so a background
        # refresh never yanks a handle out from under the user.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._mem_offset_cfg_key = None
        # NVML probe cache + wake-transition tracking. Probing is an NVML open,
        # so it is limited to reloads and wake transitions (see _recompute_normal_max).
        self._probed: dict = {}
        self._was_awake = False
        self._gpu_awake = False
        self._init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1500)

    # ── lifecycle ──
    def reload_from_config(self) -> None:
        # Force a fresh mirror of the config into the sliders.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._mem_offset_cfg_key = None
        # Refresh first so the awake flag (a free sysfs read) is current before
        # the probe decision is made; otherwise the initial load would look like a
        # wake transition and probe a tick later instead.
        self._refresh()
        self._read_published(force_probe=True)
        self._refresh()

    def _read_published(self, force_probe: bool = False) -> None:
        """Refresh the read-only data this page shows, from the state file.

        The live offset and the applied values come from the state file the
        watcher publishes, so a refresh costs a file read and — crucially — never
        touches NVML. NVML takes a power-management reference on the dGPU, so a
        UI that polled it would resume a card the kernel had deliberately powered
        down; that is exactly how this page used to stop a laptop's dGPU from
        sleeping. Probing is therefore limited to the cases listed in
        ``_recompute_normal_max`` and only while the dGPU is awake.
        """
        try:
            state = dgpu_tune.read_state_file() or {}
        except Exception:
            state = {}
        self._hw = state
        self._recompute_normal_max(force=force_probe)

    def _recompute_normal_max(self, force: bool = False) -> None:
        """Track the highest reported GPU max clock and add headroom.

        ``nvmlDeviceGetMaxClockInfo`` is power-state dependent (lower when idle),
        so we keep a high-water mark plus headroom; this makes the slider's top
        genuinely mean "unlocked" even under load.

        A probe costs an NVML open, so it only happens when it can change
        something: on an explicit reload, when nothing is cached yet, or on a
        wake transition (the ceiling is typically higher once the GPU is loaded).
        Never on a timer, and never while the dGPU is suspended.
        """
        probed = self._probed
        awake = self._gpu_awake
        prev_range = (self._normal_min, self._normal_max)
        want_probe = force or not probed or (awake and not self._was_awake)
        self._was_awake = awake
        if want_probe and awake:
            try:
                probed = dgpu_tune.probe(force=True)
            except Exception:
                probed = dgpu_tune.probe_if_awake()
            self._probed = probed
        if not probed:
            # Nothing cached and the GPU is asleep: fall back to the cached probe
            # (cheap) so the sliders still have a sane range.
            try:
                probed = dgpu_tune.probe_if_awake()
            except Exception:
                probed = {}
            self._probed = probed
        ceiling = probed.get("gpu_max_clock_mhz")
        if ceiling:
            self._observed_ceiling = max(self._observed_ceiling, int(ceiling))
        if self._observed_ceiling <= 0:
            self._observed_ceiling = 3000  # sensible fallback if unreadable
        self._normal_max = self._observed_ceiling + dgpu_tune.MAX_CLOCK_HEADROOM
        self._normal_min = max(300, self._observed_ceiling - dgpu_tune.MAX_CLOCK_SPAN)
        if (self._normal_min, self._normal_max) != prev_range:
            # Only touch the slider when the range really moved, and drop the
            # mirror memo so _sync_max_controls re-applies its own (possibly
            # widened) range instead of being skipped as "already up to date".
            self.max_slider.setRange(self._normal_min, self._normal_max)
            self._max_cfg_key = None

    def _mem_offset(self) -> int:
        """The memory-clock V/F offset (signed MHz) the slider points at."""
        return int(self.mem_slider.value())

    def _on_tick(self) -> None:
        if not self.isVisible():
            return
        self._tick_count += 1
        if self._tick_count % self._POLL_TICKS == 0:
            # A file read only: the refresh must never wake a sleeping dGPU.
            self._read_published()
        self._refresh()

    # ── UI ──
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        layout.addWidget(self._build_state_card())
        layout.addWidget(self._build_core_clock_card())
        layout.addWidget(self._build_mem_clock_card())
        layout.addLayout(self._build_apply_bar())
        layout.addStretch()

    def _build_state_card(self) -> QFrame:
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
        return state_card

    def _build_core_clock_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "Card")
        box = QVBoxLayout(card)
        box.setSpacing(14)

        title = QLabel("Core Clock Tuning (Undervolt & Curve Flattening)")
        title.setProperty("class", "CardTitle")
        box.addWidget(title)

        tech_explainer = QLabel(
            "<b>How modern NVIDIA undervolting works:</b><br/>"
            "• <b>Curve Offset (Overclock / Undervolt):</b> Shifts the voltage/frequency curve upward so the core reaches higher clock speeds at lower voltages, reducing heat and power draw.<br/>"
            "• <b>Boost Clock Cap (Flattening):</b> An aggressive offset pushes top frequencies into unstable territory under peak boost. Capping the max clock \"flattens\" the top of the curve, keeping the efficiency gains of the undervolt while maintaining total stability."
        )
        tech_explainer.setStyleSheet("color: #94a3b8; font-size: 11px; line-height: 1.4;")
        tech_explainer.setWordWrap(True)
        box.addWidget(tech_explainer)

        self.uv_support_lbl = QLabel("")
        self.uv_support_lbl.setStyleSheet("color: #8896ab; font-size: 11px;")
        self.uv_support_lbl.setWordWrap(True)
        box.addWidget(self.uv_support_lbl)

        # ── Sub-section 1: V/F Curve Offset ──
        uv_sub_title = QLabel("1. V/F Curve Offset (Core Undervolt)")
        uv_sub_title.setStyleSheet("color: #e2e8f0; font-size: 13px; font-weight: 600; margin-top: 4px;")
        box.addWidget(uv_sub_title)

        uv_sub_desc = QLabel(
            "Shift frequency up across all voltage points (0 to +255 MHz). "
            "Higher values reduce voltage for any given clock."
        )
        uv_sub_desc.setStyleSheet("color: #718096; font-size: 11px;")
        uv_sub_desc.setWordWrap(True)
        box.addWidget(uv_sub_desc)

        uv_row = QHBoxLayout()
        uv_row.setSpacing(10)
        self.uv_slider = _SliderNoWheel(Qt.Orientation.Horizontal)
        self.uv_slider.setMinimum(0)
        self.uv_slider.setMaximum(dgpu_tune.MAX_OFFSET_MHZ)
        self.uv_slider.setSingleStep(5)
        self.uv_slider.setValue(0)
        self.uv_slider.valueChanged.connect(self._on_uv_slider)
        self.uv_val = QLabel("+0 MHz")
        self.uv_val.setProperty("class", "ValueReadoutPill")
        self.uv_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.uv_val.setMinimumWidth(80)
        self.uv_reset_btn = QPushButton("Reset")
        self.uv_reset_btn.setToolTip("Return the V/F offset to stock (0 MHz) now")
        self.uv_reset_btn.clicked.connect(lambda: self._reset_uv())
        uv_row.addWidget(self.uv_slider, 1)
        uv_row.addWidget(self.uv_val)
        uv_row.addWidget(self.uv_reset_btn)
        box.addLayout(uv_row)

        self.uv_status_lbl = QLabel("")
        self.uv_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px; font-weight: 500;")
        box.addWidget(self.uv_status_lbl)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        divider.setStyleSheet("color: #232b3d; margin-top: 6px; margin-bottom: 6px;")
        box.addWidget(divider)

        # ── Sub-section 2: Max Clock Cap (Curve Flattening) ──
        mc_sub_title = QLabel("2. Max Boost Clock Cap (Curve Flattening)")
        mc_sub_title.setStyleSheet("color: #e2e8f0; font-size: 13px; font-weight: 600;")
        box.addWidget(mc_sub_title)

        mc_sub_desc = QLabel(
            "Upper-bound the boost clock. The top of the slider is Stock (unlocked). "
            "Lowering it flattens the curve above the specified MHz to prevent instability."
        )
        mc_sub_desc.setStyleSheet("color: #718096; font-size: 11px;")
        mc_sub_desc.setWordWrap(True)
        box.addWidget(mc_sub_desc)

        mc_row = QHBoxLayout()
        mc_row.setSpacing(10)
        self.max_slider = _SliderNoWheel(Qt.Orientation.Horizontal)
        self.max_slider.setRange(self._normal_min, self._normal_max)
        self.max_slider.setSingleStep(25)
        self.max_slider.setValue(self._normal_max)
        self.max_slider.valueChanged.connect(self._on_max_slider)
        self.max_val = QLabel("Stock (unlocked)")
        self.max_val.setProperty("class", "ValueReadoutPill")
        self.max_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.max_val.setMinimumWidth(160)
        self.max_reset_btn = QPushButton("Reset")
        self.max_reset_btn.setToolTip("Unlock the boost clock now")
        self.max_reset_btn.clicked.connect(lambda: self._reset_max())
        mc_row.addWidget(self.max_slider, 1)
        mc_row.addWidget(self.max_val)
        mc_row.addWidget(self.max_reset_btn)
        box.addLayout(mc_row)

        self.max_status_lbl = QLabel("")
        self.max_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px; font-weight: 500;")
        box.addWidget(self.max_status_lbl)

        return card

    def _build_mem_clock_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "Card")
        box = QVBoxLayout(card)
        box.setSpacing(10)

        title = QLabel("Memory Clock (VRAM Overclock)")
        title.setProperty("class", "CardTitle")
        box.addWidget(title)

        note = QLabel(
            "Independent memory frequency offset. Positive values overclock VRAM to increase "
            "memory bandwidth (improving bandwidth-heavy games and workloads), negative values "
            "underclock, and 0 is stock. The GPU driver automatically validates supported offsets."
        )
        note.setStyleSheet("color: #8896ab; font-size: 11px; line-height: 1.4;")
        note.setWordWrap(True)
        box.addWidget(note)

        self.mem_support_lbl = QLabel("")
        self.mem_support_lbl.setStyleSheet("color: #8896ab; font-size: 11px;")
        self.mem_support_lbl.setWordWrap(True)
        box.addWidget(self.mem_support_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.mem_slider = _SliderNoWheel(Qt.Orientation.Horizontal)
        self.mem_slider.setRange(dgpu_tune.MEM_OFFSET_MIN_MHZ, dgpu_tune.MEM_OFFSET_MAX_MHZ)
        self.mem_slider.setSingleStep(25)
        self.mem_slider.setPageStep(100)
        self.mem_slider.setValue(0)
        self.mem_slider.valueChanged.connect(self._on_mem_slider)
        self.mem_val = QLabel("+0 MHz")
        self.mem_val.setProperty("class", "ValueReadoutPill")
        self.mem_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mem_val.setMinimumWidth(110)
        self.mem_reset_btn = QPushButton("Reset")
        self.mem_reset_btn.setToolTip("Return the memory clock offset to stock now")
        self.mem_reset_btn.clicked.connect(lambda: self._reset_mem())
        row.addWidget(self.mem_slider, 1)
        row.addWidget(self.mem_val)
        row.addWidget(self.mem_reset_btn)
        box.addLayout(row)

        self.mem_status_lbl = QLabel("")
        self.mem_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px; font-weight: 500;")
        box.addWidget(self.mem_status_lbl)
        return card

    def _build_apply_bar(self) -> QHBoxLayout:
        """The single commit point for all three controls."""
        row = QHBoxLayout()
        row.setSpacing(12)
        self.apply_btn = QPushButton("Apply Tuning")
        self.apply_btn.setProperty("class", "PrimaryButton")
        self.apply_btn.setMinimumHeight(38)
        self.apply_btn.setToolTip("Apply the core offset, boost clock cap and memory offset together")
        self.apply_btn.clicked.connect(self._apply_all)
        row.addWidget(self.apply_btn)
        self.apply_hint_lbl = QLabel("")
        self.apply_hint_lbl.setStyleSheet("color: #8896ab; font-size: 11px; font-weight: 500;")
        self.apply_hint_lbl.setWordWrap(True)
        row.addWidget(self.apply_hint_lbl, 1)
        return row

    # ── slider handlers ──
    def _on_uv_slider(self, value: int) -> None:
        self.uv_val.setText(f"+{value} MHz")
        self._update_apply_hint()

    def _on_max_slider(self, value: int) -> None:
        self._update_max_readout(value)
        self._update_apply_hint()

    def _on_mem_slider(self, _value: int) -> None:
        self._update_mem_readout()
        self._update_apply_hint()

    def _update_max_readout(self, value: int) -> None:
        if value >= self._observed_ceiling:
            self.max_val.setText("Stock (unlocked)")
        else:
            self.max_val.setText(f"Cap {value} MHz (−{self._observed_ceiling - value})")

    def _update_mem_readout(self) -> None:
        offset = self._mem_offset()
        if offset:
            self.mem_val.setText(f"{offset:+d} MHz")
        else:
            self.mem_val.setText("+0 MHz (stock)")

    def _update_apply_hint(self) -> None:
        """Tell the user when the sliders no longer match what is configured."""
        staged = []
        if self._uv_cfg_key is not None and self.uv_slider.value() != self._uv_cfg_key[1]:
            staged.append("undervolt")
        if self._max_cfg_key is not None and self.max_slider.value() != self._max_cfg_key[1]:
            staged.append("max clock")
        if self._mem_offset_cfg_key is not None and self._mem_offset() != self._mem_offset_cfg_key:
            staged.append("memory offset")
        if staged:
            self.apply_hint_lbl.setText(
                "Unapplied: " + ", ".join(staged) + " — press Apply to send them to the GPU.")
        else:
            self.apply_hint_lbl.setText(
                "Sliders match the saved settings. Tuning is applied while the dGPU "
                "is awake and in use; it pauses when the dGPU is idle or asleep.")

    # ── actions ──
    def _apply_all(self) -> None:
        """Commit all three controls in a single apply (one config write)."""
        offset = max(0, min(dgpu_tune.MAX_OFFSET_MHZ, int(self.uv_slider.value())))
        cap = max(self.max_slider.minimum(),
                  min(self.max_slider.maximum(), int(self.max_slider.value())))
        mem = self._mem_offset()
        # A control that is "off" must be sent as 0, not as the handle's resting
        # position, so the request reads the same way the config will store it.
        cap_enabled = cap < self._observed_ceiling
        try:
            dgpu_tune.set_desired_config(
                enabled=offset > 0, offset=offset,
                max_enabled=cap_enabled, max_clock=cap if cap_enabled else 0,
                mem_enabled=mem != 0, mem_offset=mem,
            )
        except Exception:
            pass
        # The config now matches the sliders, so re-mirror from it.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._mem_offset_cfg_key = None
        self._read_published()
        self._refresh()

    def _reset_uv(self) -> None:
        """Reset (stock) takes effect immediately, for this control only."""
        try:
            dgpu_tune.set_desired_config(enabled=False, offset=0)
        except Exception:
            pass
        self.uv_slider.blockSignals(True)
        self.uv_slider.setValue(0)
        self.uv_slider.blockSignals(False)
        self.uv_val.setText("+0 MHz")
        self._uv_cfg_key = None
        self._read_published()
        self._refresh()

    def _reset_max(self) -> None:
        try:
            dgpu_tune.set_desired_config(max_enabled=False, max_clock=0)
        except Exception:
            pass
        self.max_slider.blockSignals(True)
        self.max_slider.setValue(self._normal_max)
        self.max_slider.blockSignals(False)
        self._update_max_readout(self.max_slider.value())
        self._max_cfg_key = None
        self._read_published()
        self._refresh()

    def _reset_mem(self) -> None:
        try:
            dgpu_tune.set_desired_config(mem_enabled=False, mem_offset=0)
        except Exception:
            pass
        self.mem_slider.blockSignals(True)
        self.mem_slider.setValue(0)
        self.mem_slider.blockSignals(False)
        self.mem_val.setText("+0 MHz (stock)")
        self._mem_offset_cfg_key = None
        self._read_published()
        self._refresh()

    # ── refresh ──
    def _refresh(self) -> None:
        gpu = get_gpu_state()
        # Free sysfs read; every NVML decision on this page keys off it.
        self._gpu_awake = bool(gpu.present and gpu.status == "active")
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
        # Cached-or-nothing probe: never spawns a helper while the dGPU sleeps.
        probed = self._probed
        try:
            probed = dgpu_tune.probe_if_awake() or probed
        except Exception:
            pass
        if probed:
            self._probed = probed
        supported = bool(probed.get("supported"))

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
        self.uv_reset_btn.setEnabled(supported)
        self.max_slider.setEnabled(supported)
        self.max_reset_btn.setEnabled(supported)

        if supported:
            self._sync_uv_controls()
            self._sync_max_controls()
            self._sync_mem_controls()
        else:
            self.uv_status_lbl.setText("")
            self.max_status_lbl.setText("")
            self.mem_status_lbl.setText("")
        self.apply_btn.setEnabled(supported)
        self._update_apply_hint()

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

        st = self._hw or {}
        idle_cleared = bool(st.get("idle_cleared"))
        auto = st.get("auto", True)
        if enabled and offset > 0 and hw_off == offset:
            state = f"Applied: +{offset} MHz"
        elif enabled and offset > 0 and not self._gpu_awake:
            # The hardware reads 0 while the dGPU is asleep (D3cold wiped it), so
            # report what is configured rather than pretending it is off.
            state = f"Configured: +{offset} MHz — applies when the dGPU wakes"
        elif enabled and offset > 0 and idle_cleared:
            state = (f"Configured: +{offset} MHz — paused while the dGPU is idle "
                     "(re-applies under load)")
        elif enabled and offset > 0 and not auto:
            state = f"Configured: +{offset} MHz — pending (auto-apply off)"
        elif enabled and offset > 0:
            state = f"Configured: +{offset} MHz — pending apply"
        elif hw_off not in (None, 0):
            state = f"Stock requested; hardware still at +{hw_off} MHz"
        else:
            state = "Off (stock)"
        self.uv_status_lbl.setText(state)

    def _sync_max_controls(self) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_max_clock_enabled", False))
        wanted = int(cfg.get("dgpu_max_clock_mhz", 0) or 0)
        st = self._hw or {}
        applied_max = st.get("applied_max") or 0
        idle_cleared = bool(st.get("idle_cleared"))
        auto = st.get("auto", True)

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
        elif enabled and wanted > 0 and not self._gpu_awake:
            state = f"Configured: cap {wanted} MHz — applies when the dGPU wakes"
        elif enabled and wanted > 0 and idle_cleared:
            state = (f"Configured: cap {wanted} MHz — paused while the dGPU is idle "
                     "(re-applies under load)")
        elif enabled and wanted > 0 and not auto:
            state = f"Configured: cap {wanted} MHz — pending (auto-apply off)"
        elif enabled and wanted > 0:
            state = f"Configured: cap {wanted} MHz — pending apply"
        elif applied_max:
            state = f"Off requested; hardware still capped at {applied_max} MHz"
        else:
            state = "Off (unlocked)"
        self.max_status_lbl.setText(state)

    def _sync_mem_controls(self) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_mem_offset_enabled", False))
        wanted = int(cfg.get("dgpu_mem_offset_mhz", 0) or 0)
        st = self._hw or {}
        applied = st.get("applied_mem") or 0
        idle_cleared = bool(st.get("idle_cleared"))
        auto = st.get("auto", True)
        # Read the helper version from the probe cache: asking for it must never
        # open NVML on a card the kernel has powered down. An unknown version is
        # not treated as stale — we simply have nothing to say.
        try:
            version = dgpu_tune.cached_helper_version()
        except Exception:
            version = None
        stale_helper = (version is not None
                        and version < dgpu_tune.REQUIRED_HELPER_VERSION)

        offset_api = bool(self._probed.get("mem_offset_api"))
        mem_supported = offset_api and not stale_helper
        if stale_helper:
            self.mem_support_lbl.setText(
                "The installed helper is too old for the memory-clock offset — "
                "re-run ./install.sh to update it.")
        elif not offset_api:
            self.mem_support_lbl.setText(
                "This GPU/driver does not support the memory-clock offset API.")
        else:
            self.mem_support_lbl.setText(
                f"Signed offset {dgpu_tune.MEM_OFFSET_MIN_MHZ:+d}.."
                f"{dgpu_tune.MEM_OFFSET_MAX_MHZ:+d} MHz "
                "(+ overclocks, − underclocks, 0 = stock).")

        self.mem_slider.setEnabled(mem_supported)
        self.mem_reset_btn.setEnabled(mem_supported)

        # Mirror the configured offset into the slider.
        target = wanted if enabled else 0
        if target != self._mem_offset_cfg_key:
            if not self.mem_slider.isSliderDown():
                self.mem_slider.blockSignals(True)
                self.mem_slider.setValue(target)
                self.mem_slider.blockSignals(False)
                self._update_mem_readout()
            self._mem_offset_cfg_key = target

        if not mem_supported:
            self.mem_status_lbl.setText("")
            return
        if enabled and wanted != 0 and applied == wanted:
            state = f"Applied: {wanted:+d} MHz"
        elif enabled and wanted != 0 and not self._gpu_awake:
            state = f"Configured: {wanted:+d} MHz — applies when the dGPU wakes"
        elif enabled and wanted != 0 and idle_cleared:
            state = (f"Configured: {wanted:+d} MHz — paused while the dGPU is idle "
                     "(re-applies under load)")
        elif enabled and wanted != 0 and not auto:
            state = f"Configured: {wanted:+d} MHz — pending (auto-apply off)"
        elif enabled and wanted != 0:
            state = f"Configured: {wanted:+d} MHz — pending apply"
        elif applied:
            state = f"Off requested; hardware still at {applied:+d} MHz"
        else:
            state = "Off (stock)"
        self.mem_status_lbl.setText(state)
