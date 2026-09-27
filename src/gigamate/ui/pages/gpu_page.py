"""GigaMate Center — Discrete GPU (NVIDIA) sleep-aware tuning.

Three independent, sleep-aware controls, each a slider with its readout and a
Reset (stock) button beside it:

* Undervolt (V/F curve offset) — slider from 0 to +255 MHz.
* Max clock cap — slider from (normal max - 800) MHz up to a little above the
  normal max; the top of the travel is "Stock (unlocked)".
* Memory clock — a slider stepping through the memory clocks the GPU reports,
  so it can only ever land on a value the hardware accepts. The top is the
  highest supported clock (a guaranteed-max pin); lower values cap it.

A single **Apply** button at the bottom commits all three in one go (one config
write, one helper call). Moving a slider only stages a value — nothing reaches
the hardware until Apply. Reset sits next to each slider and takes effect
immediately, since "put this one back to stock" is unambiguous on its own.

This page shows what is *configured and applied*, never live hardware telemetry:
temperature, clocks, power and VRAM are MangoHud's and nvtop's job, and every
NVML read risks resuming a dGPU the kernel has powered down. The applied values
come from the state file the watcher service publishes, so a refresh is a file
read and can never wake the GPU; NVML is touched only for a probe, and only
while the dGPU is already awake.

The cap can only *lower* the boost clock: NVML's locked-clock API bounds clocks
within the GPU's already-permitted range, so it cannot raise them. The memory
clock behaves the same way — it can only select a clock the GPU already
supports. All controls apply only while the dGPU is awake and are cleared on
suspend / idle / reboot.
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
        self._mem_choices: list = []
        # Config values currently mirrored into the widgets. Sliders are only
        # repositioned when this changes (and never mid-drag), so a background
        # refresh never yanks a handle out from under the user.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._mem_cfg_key = None
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
        self._mem_cfg_key = None
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
        self._recompute_mem_choices(probed)

    def _recompute_mem_choices(self, probed: dict) -> None:
        """Re-range the memory slider over the clocks the GPU actually reports."""
        choices = [int(c) for c in (probed.get("mem_supported_clocks") or [])
                   if int(c) >= dgpu_tune.MEM_CLOCK_MIN_PIN_MHZ]
        if not choices:
            top = probed.get("mem_max_clock_mhz")
            choices = [int(top)] if top else []
        choices = sorted(set(choices))
        if choices == self._mem_choices:
            return
        self._mem_choices = choices
        # Index-based steps: the slider can only land on a supported clock.
        self.mem_slider.setRange(0, max(0, len(choices) - 1))
        self.mem_slider.setSingleStep(1)
        self.mem_slider.setPageStep(1)
        if choices:
            self.mem_slider.blockSignals(True)
            self.mem_slider.setValue(len(choices) - 1)
            self.mem_slider.blockSignals(False)
        self._update_mem_readout()
        self._mem_cfg_key = None

    def _mem_clock(self) -> int:
        """The memory clock the slider currently points at (0 = no choice)."""
        if not self._mem_choices:
            return 0
        idx = max(0, min(self.mem_slider.value(), len(self._mem_choices) - 1))
        return self._mem_choices[idx]

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
        layout.addWidget(self._build_undervolt_card())
        layout.addWidget(self._build_max_clock_card())
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

    def _build_undervolt_card(self) -> QFrame:
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
        uv_row.setSpacing(8)
        self.uv_slider = _SliderNoWheel(Qt.Orientation.Horizontal)
        self.uv_slider.setMinimum(0)
        self.uv_slider.setMaximum(dgpu_tune.MAX_OFFSET_MHZ)
        self.uv_slider.setSingleStep(5)
        self.uv_slider.setValue(0)
        self.uv_slider.valueChanged.connect(self._on_uv_slider)
        self.uv_val = QLabel("+0 MHz")
        self.uv_val.setStyleSheet("color: #ffffff; font-weight: 700; min-width: 70px;")
        self.uv_reset_btn = QPushButton("Reset")
        self.uv_reset_btn.setToolTip("Return the V/F offset to stock (0 MHz) now")
        self.uv_reset_btn.clicked.connect(lambda: self._reset_uv())
        uv_row.addWidget(self.uv_slider, 1)
        uv_row.addWidget(self.uv_val)
        uv_row.addWidget(self.uv_reset_btn)
        uc.addLayout(uv_row)

        self.uv_status_lbl = QLabel("")
        self.uv_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px;")
        uc.addWidget(self.uv_status_lbl)

        uv_note = QLabel(
            "Higher offset = lower voltage for a given clock. If the GPU becomes "
            "unstable, lower the value. Applied only while the dGPU is awake; "
            "cleared automatically when it suspends and reset on reboot."
        )
        uv_note.setStyleSheet("color: #718096; font-size: 11px;")
        uv_note.setWordWrap(True)
        uc.addWidget(uv_note)
        return uv_card

    def _build_max_clock_card(self) -> QFrame:
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
        mc_row.setSpacing(8)
        self.max_slider = _SliderNoWheel(Qt.Orientation.Horizontal)
        self.max_slider.setRange(self._normal_min, self._normal_max)
        self.max_slider.setSingleStep(25)
        self.max_slider.setValue(self._normal_max)
        self.max_slider.valueChanged.connect(self._on_max_slider)
        self.max_val = QLabel("Stock (unlocked)")
        self.max_val.setStyleSheet("color: #ffffff; font-weight: 700; min-width: 150px;")
        self.max_reset_btn = QPushButton("Reset")
        self.max_reset_btn.setToolTip("Unlock the boost clock now")
        self.max_reset_btn.clicked.connect(lambda: self._reset_max())
        mc_row.addWidget(self.max_slider, 1)
        mc_row.addWidget(self.max_val)
        mc_row.addWidget(self.max_reset_btn)
        mcc.addLayout(mc_row)

        self.max_status_lbl = QLabel("")
        self.max_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px;")
        mcc.addWidget(self.max_status_lbl)
        return mc_card

    def _build_mem_clock_card(self) -> QFrame:
        card = QFrame()
        card.setProperty("class", "Card")
        box = QVBoxLayout(card)
        box.setSpacing(10)

        title = QLabel("Memory clock")
        title.setProperty("class", "CardTitle")
        box.addWidget(title)
        note = QLabel(
            "Pin the memory clock to one of the values this GPU supports. The top of "
            "the slider is the highest one (a guaranteed-max pin); lower values cap "
            "it to save power and heat. It cannot go beyond the GPU's own maximum, "
            "and it needs the dGPU to be awake."
        )
        note.setStyleSheet("color: #8896ab; font-size: 11px;")
        note.setWordWrap(True)
        box.addWidget(note)

        self.mem_support_lbl = QLabel("")
        self.mem_support_lbl.setStyleSheet("color: #8896ab; font-size: 11px;")
        self.mem_support_lbl.setWordWrap(True)
        box.addWidget(self.mem_support_lbl)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.mem_slider = _SliderNoWheel(Qt.Orientation.Horizontal)
        self.mem_slider.setRange(0, 0)
        self.mem_slider.setValue(0)
        self.mem_slider.valueChanged.connect(self._on_mem_slider)
        self.mem_val = QLabel("—")
        self.mem_val.setStyleSheet("color: #ffffff; font-weight: 700; min-width: 130px;")
        self.mem_reset_btn = QPushButton("Reset")
        self.mem_reset_btn.setToolTip("Return the memory clock to the driver default now")
        self.mem_reset_btn.clicked.connect(lambda: self._reset_mem())
        row.addWidget(self.mem_slider, 1)
        row.addWidget(self.mem_val)
        row.addWidget(self.mem_reset_btn)
        box.addLayout(row)

        self.mem_status_lbl = QLabel("")
        self.mem_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px;")
        box.addWidget(self.mem_status_lbl)
        return card

    def _build_apply_bar(self) -> QHBoxLayout:
        """The single commit point for all three controls."""
        row = QHBoxLayout()
        row.setSpacing(8)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setProperty("class", "ProfileButton")
        self.apply_btn.setToolTip("Apply the undervolt, clock cap and memory clock together")
        self.apply_btn.clicked.connect(self._apply_all)
        row.addWidget(self.apply_btn)
        self.apply_hint_lbl = QLabel("")
        self.apply_hint_lbl.setStyleSheet("color: #718096; font-size: 11px;")
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
        mhz = self._mem_clock()
        if not mhz:
            self.mem_val.setText("—")
        elif self._mem_choices and mhz == self._mem_choices[-1]:
            self.mem_val.setText(f"{mhz} MHz (max)")
        else:
            self.mem_val.setText(f"{mhz} MHz")

    def _update_apply_hint(self) -> None:
        """Tell the user when the sliders no longer match what is configured."""
        staged = []
        if self._uv_cfg_key is not None and self.uv_slider.value() != self._uv_cfg_key[1]:
            staged.append("undervolt")
        if self._max_cfg_key is not None and self.max_slider.value() != self._max_cfg_key[1]:
            staged.append("max clock")
        if self._mem_cfg_key is not None and self._mem_clock() != self._mem_cfg_key:
            staged.append("memory clock")
        if staged:
            self.apply_hint_lbl.setText(
                "Unapplied: " + ", ".join(staged) + " — press Apply to send them to the GPU.")
        else:
            self.apply_hint_lbl.setText(
                "Sliders match the saved settings. Everything is applied only while "
                "the dGPU is awake.")

    # ── actions ──
    def _apply_all(self) -> None:
        """Commit all three controls in a single apply (one config write)."""
        offset = max(0, min(dgpu_tune.MAX_OFFSET_MHZ, int(self.uv_slider.value())))
        cap = max(self.max_slider.minimum(),
                  min(self.max_slider.maximum(), int(self.max_slider.value())))
        mem = self._mem_clock()
        # A control that is "off" must be sent as 0, not as the handle's resting
        # position, so the request reads the same way the config will store it.
        cap_enabled = cap < self._observed_ceiling
        try:
            dgpu_tune.set_desired_config(
                enabled=offset > 0, offset=offset,
                max_enabled=cap_enabled, max_clock=cap if cap_enabled else 0,
                mem_enabled=mem > 0, mem_clock=mem,
            )
        except Exception:
            pass
        # The config now matches the sliders, so re-mirror from it.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._mem_cfg_key = None
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
            dgpu_tune.set_desired_config(mem_enabled=False, mem_clock=0)
        except Exception:
            pass
        self._mem_cfg_key = None
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

        if enabled and offset > 0 and hw_off == offset:
            state = f"Applied: +{offset} MHz"
        elif enabled and offset > 0 and not self._gpu_awake:
            # The hardware reads 0 while the dGPU is asleep (D3cold wiped it), so
            # report what is configured rather than pretending it is off.
            state = f"Configured: +{offset} MHz — applies when the dGPU wakes"
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
        elif enabled and wanted > 0 and not self._gpu_awake:
            state = f"Configured: cap {wanted} MHz — applies when the dGPU wakes"
        elif enabled and wanted > 0:
            state = "Configured — not applied yet (dGPU asleep or pending)"
        elif applied_max:
            state = f"Off requested; hardware still capped at {applied_max} MHz"
        else:
            state = "Off (unlocked)"
        self.max_status_lbl.setText(state)

    def _sync_mem_controls(self) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_mem_clock_enabled", False))
        wanted = int(cfg.get("dgpu_mem_clock_mhz", 0) or 0)
        applied = dgpu_tune.read_state_file().get("applied_mem") or 0
        # Read the helper version from the probe cache: asking for it must never
        # open NVML on a card the kernel has powered down. An unknown version is
        # not treated as stale — we simply have nothing to say.
        try:
            version = dgpu_tune.cached_helper_version()
        except Exception:
            version = None
        stale_helper = (version is not None
                        and version < dgpu_tune.REQUIRED_HELPER_VERSION)

        mem_supported = bool(self._mem_choices) and not stale_helper
        if not self._mem_choices:
            self.mem_support_lbl.setText(
                "This GPU/driver did not report any selectable memory clock.")
        elif stale_helper:
            self.mem_support_lbl.setText(
                "The installed helper is too old for memory-clock control — "
                "re-run ./install.sh to update it.")
        else:
            self.mem_support_lbl.setText(
                "This GPU supports: " + ", ".join(f"{c}" for c in self._mem_choices) + " MHz.")

        self.mem_slider.setEnabled(mem_supported)
        self.mem_reset_btn.setEnabled(mem_supported)

        # Mirror the configured pin into the slider (top of travel when off, so
        # the handle always rests on a real clock).
        target = wanted if (enabled and wanted in self._mem_choices) else 0
        if target != self._mem_cfg_key:
            if not self.mem_slider.isSliderDown():
                pos = self._mem_choices.index(target) if target else max(
                    0, len(self._mem_choices) - 1)
                self.mem_slider.blockSignals(True)
                self.mem_slider.setValue(pos)
                self.mem_slider.blockSignals(False)
                self._update_mem_readout()
            self._mem_cfg_key = target

        if not mem_supported:
            self.mem_status_lbl.setText("")
            return
        selected = self._mem_clock()
        if enabled and wanted > 0 and applied == wanted:
            state = f"Applied: pinned at {wanted} MHz"
        elif enabled and wanted > 0 and not self._gpu_awake:
            state = f"Configured: pin {wanted} MHz — applies when the dGPU wakes"
        elif enabled and wanted > 0:
            state = "Configured — not applied yet (dGPU asleep or pending)"
        elif applied:
            state = f"Off requested; hardware still pinned at {applied} MHz"
        elif not enabled and selected != self._mem_cfg_key:
            state = f"Staged: pin {selected} MHz — press Apply"
        else:
            state = "Off (driver default)"
        self.mem_status_lbl.setText(state)
