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

import queue
import threading
import time
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import dgpu_gate, dgpu_tune
from ...battery import get_battery_manager
from ...config import load as load_config, update_config
from ...gpu import get_gpu_state, gpu_status_text
from .. import icons
from ..theme import THEME
from ..widgets import (
    M3Card,
    M3IconHelpButton,
    M3PageHeader,
    M3SegmentButton,
    M3SegmentedBar,
    M3Slider,
    M3StatusPill,
)


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
        self._holders_results = queue.Queue()
        self._holders_busy = False
        self._holders_checked_at = None
        self._holders = []
        self._holders_timer = QTimer(self)
        self._holders_timer.setInterval(100)
        self._holders_timer.timeout.connect(self._collect_holders)
        self._gate_supported: Optional[bool] = None
        self._gate_buttons: dict[str, QPushButton] = {}
        self._gate_results: queue.Queue = queue.Queue()
        self._gate_busy = False
        self._controls_enabled: bool = False
        self._mem_controls_enabled: bool = False
        self._is_gated: bool = False
        self._gate_timer = QTimer(self)
        self._gate_timer.setInterval(100)
        self._gate_timer.timeout.connect(self._collect_gate_result)
        self._init_ui()

        # Prime the watcher state before the first timer tick so the dGPU-asleep
        # fallback has valid data from the very first _refresh call.
        try:
            self._hw = dgpu_tune.read_state_file() or {}
        except Exception:
            pass

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(1500)

    # ── lifecycle ──
    def reload_from_config(self) -> None:
        # Force a fresh mirror of the config into the sliders.
        self._uv_cfg_key = None
        self._max_cfg_key = None
        self._mem_offset_cfg_key = None
        # Load the watcher state file first so self._hw is populated before the
        # first _refresh. This ensures the dGPU-asleep fallback (reading
        # supported/device from the state file rather than from an NVML probe)
        # is available on the very first render.
        self._gate_supported = None
        self._read_published()
        self._refresh(recheck_gate=True)
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
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(
            M3PageHeader(
                "GPU Tuning",
                "Discrete GPU power gating & sleep-aware NVIDIA clock tuning",
            )
        )
        layout.addWidget(self._build_state_card())
        layout.addWidget(self._build_power_gate_card())
        layout.addWidget(self._build_core_clock_card())
        layout.addWidget(self._build_mem_clock_card())
        layout.addWidget(self._build_apply_bar())
        layout.addStretch()

    def _build_power_gate_card(self) -> M3Card:
        self.power_gate_card = M3Card()
        box = QVBoxLayout(self.power_gate_card)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel("dGPU Power Gate (AI Power Gear III)")
        title.setProperty("class", "CardTitle")
        hdr.addWidget(title)
        self.gate_exp_badge = M3StatusPill("Experimental", tone="warning")
        self.gate_exp_badge.setVisible(False)
        hdr.addWidget(self.gate_exp_badge)
        hdr.addStretch()
        self.gate_rail_pill = M3StatusPill("", tone="neutral")
        hdr.addWidget(self.gate_rail_pill)
        box.addLayout(hdr)

        desc = QLabel(
            "Cut the discrete GPU power rail via ACPI and remove its PCI device "
            "(~0 W draw); saved tuning re-applies automatically on wake."
        )
        desc.setProperty("class", "CardSubtitle")
        desc.setWordWrap(True)
        box.addWidget(desc)

        self.gate_bar = M3SegmentedBar()
        self.gate_btn_group = QButtonGroup(self)
        self.gate_btn_group.setExclusive(True)
        for mode_key, label, tip in (
            ("optimus", "Optimus", "Default: kernel RTD3 runtime PM manages D3cold"),
            ("eco", "ECO (dGPU Off)", "Prompt & close GPU apps if needed, then cut power rail until switched back"),
            ("auto", "Auto (Battery)", "Prompt to gate off when AC unplugs; restore automatically on AC"),
        ):
            btn = self.gate_bar.add_segment(label, tooltip=tip, checkable=True, min_height=40)
            btn.clicked.connect(lambda _checked=False, m=mode_key: self._on_gate_mode_clicked(m))
            self.gate_btn_group.addButton(btn)
            self._gate_buttons[mode_key] = btn
        box.addWidget(self.gate_bar)

        self.gate_info_lbl = QLabel(
            "Optimus: runtime D3cold  ·  ECO: rail off (~0 W)  ·  Auto: prompt on unplug, restore on AC"
        )
        self.gate_info_lbl.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        self.gate_info_lbl.setWordWrap(True)
        box.addWidget(self.gate_info_lbl)

        self.gate_status_lbl = QLabel("")
        self.gate_status_lbl.setStyleSheet(
            f"color: {THEME.success}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
        )
        self.gate_status_lbl.setWordWrap(True)
        box.addWidget(self.gate_status_lbl)

        force_row = QHBoxLayout()
        self.gate_force_close_btn = QPushButton("Close Apps && Power Off")
        self.gate_force_close_btn.setProperty("class", "DangerTonalButton")
        self.gate_force_close_btn.setIcon(
            icons.make_icon("power_off", color=THEME.error, size=15)
        )
        self.gate_force_close_btn.setIconSize(QSize(15, 15))
        self.gate_force_close_btn.setToolTip(
            "Open confirmation dialog listing user apps/services currently holding the dGPU"
        )
        self.gate_force_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gate_force_close_btn.setVisible(False)
        self.gate_force_close_btn.clicked.connect(self._on_gate_force_close_clicked)
        force_row.addWidget(self.gate_force_close_btn)
        force_row.addStretch()
        box.addLayout(force_row)

        note_row = QHBoxLayout()
        note_row.setSpacing(6)
        note_icon = QLabel()
        note_icon.setFixedSize(14, 14)
        note_icon.setPixmap(icons.render_icon_pixmap("info", color=THEME.on_surface_muted, size=14))
        note_icon.setStyleSheet("background: transparent;")
        note_row.addWidget(note_icon, alignment=Qt.AlignmentFlag.AlignTop)
        note = QLabel(
            "HDMI/DisplayPort outputs wired directly to the dGPU stop working while gated off, "
            "and audio pauses briefly (~0.5 s) while detaching HDMI audio. Reboot recovers if wake ever fails."
        )
        note.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        note.setWordWrap(True)
        note_row.addWidget(note, 1)
        box.addLayout(note_row)

        self._sync_power_gate_card(recheck_supported=True)
        return self.power_gate_card

    def _build_state_card(self) -> M3Card:
        state_card = M3Card()
        sc = QVBoxLayout(state_card)
        sc.setContentsMargins(18, 12, 18, 12)
        sc.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._gpu_icon_lbl = QLabel()
        self._gpu_icon_lbl.setFixedSize(18, 18)
        self._gpu_icon_lbl.setPixmap(icons.render_icon_pixmap("gpu", color=THEME.primary, size=18))
        self._gpu_icon_lbl.setStyleSheet("background: transparent;")
        top.addWidget(self._gpu_icon_lbl)

        t = QLabel("DISCRETE GPU")
        t.setProperty("class", "SectionLabel")
        top.addWidget(t)

        self.gpu_state_lbl = QLabel("—")
        self.gpu_state_lbl.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: {THEME.text_card_title}px; font-weight: 700; background: transparent;"
        )
        top.addWidget(self.gpu_state_lbl)
        top.addStretch()

        self.gpu_meta_lbl = M3StatusPill("", tone="neutral")
        self.gpu_meta_lbl.setVisible(False)
        top.addWidget(self.gpu_meta_lbl)
        sc.addLayout(top)

        self.gpu_holders_lbl = QLabel("")
        self.gpu_holders_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        self.gpu_holders_lbl.setWordWrap(True)
        self.gpu_holders_lbl.setVisible(False)
        sc.addWidget(self.gpu_holders_lbl)
        return state_card

    def _build_core_clock_card(self) -> M3Card:
        card = M3Card()
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel("Core Clock Tuning")
        title.setProperty("class", "CardTitle")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(
            M3IconHelpButton(
                "How modern NVIDIA undervolting works:\n"
                "• Curve Offset (Overclock / Undervolt): Shifts the V/F curve upward so the core "
                "reaches higher clock speeds at lower voltages, reducing heat and power draw.\n"
                "• Boost Clock Cap (Flattening): Capping the max clock flattens the top of the "
                "curve, keeping efficiency gains while preventing peak-boost instability."
            )
        )
        box.addLayout(hdr)

        self.uv_support_lbl = QLabel("")
        self.uv_support_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        self.uv_support_lbl.setWordWrap(True)
        box.addWidget(self.uv_support_lbl)

        # ── Sub-section 1: V/F Curve Offset ──
        uv_sub_title = QLabel("1. V/F Curve Offset")
        uv_sub_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: {THEME.text_section}px; font-weight: 600; "
            f"margin-top: 2px; background: transparent;"
        )
        box.addWidget(uv_sub_title)

        uv_sub_desc = QLabel(
            "Shift frequency up across all voltage points (0 to +255 MHz) to lower voltage at any clock."
        )
        uv_sub_desc.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        uv_sub_desc.setWordWrap(True)
        box.addWidget(uv_sub_desc)

        uv_row = QHBoxLayout()
        uv_row.setSpacing(10)
        self.uv_slider = M3Slider(Qt.Orientation.Horizontal, ignore_wheel=True)
        self.uv_slider.setMinimum(0)
        self.uv_slider.setMaximum(dgpu_tune.MAX_OFFSET_MHZ)
        self.uv_slider.setSingleStep(5)
        self.uv_slider.setValue(0)
        self.uv_slider.valueChanged.connect(self._on_uv_slider)
        self.uv_val = QLabel("+0 MHz")
        self.uv_val.setProperty("class", "ValueReadoutPill")
        self.uv_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.uv_val.setMinimumWidth(86)
        self.uv_reset_btn = QPushButton("Reset")
        self.uv_reset_btn.setProperty("class", "OutlinedButton")
        self.uv_reset_btn.setIcon(icons.make_icon("undo", color=THEME.on_surface_variant, size=14))
        self.uv_reset_btn.setIconSize(QSize(14, 14))
        self.uv_reset_btn.setToolTip("Return the V/F offset to stock (0 MHz) now")
        self.uv_reset_btn.clicked.connect(lambda: self._reset_uv())
        uv_row.addWidget(self.uv_slider, 1)
        uv_row.addWidget(self.uv_val)
        uv_row.addWidget(self.uv_reset_btn)
        box.addLayout(uv_row)

        self.uv_status_lbl = QLabel("")
        self.uv_status_lbl.setStyleSheet(
            f"color: {THEME.success}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
        )
        box.addWidget(self.uv_status_lbl)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(
            f"background-color: {THEME.divider}; max-height: 1px; border: none; margin: 4px 0;"
        )
        box.addWidget(divider)

        # ── Sub-section 2: Max Clock Cap (Curve Flattening) ──
        mc_sub_title = QLabel("2. Max Boost Clock Cap")
        mc_sub_title.setStyleSheet(
            f"color: {THEME.on_surface}; font-size: {THEME.text_section}px; font-weight: 600; background: transparent;"
        )
        box.addWidget(mc_sub_title)

        mc_sub_desc = QLabel(
            "Upper-bound the boost clock; top of slider is Stock (unlocked). Lowering flattens the top of the curve."
        )
        mc_sub_desc.setStyleSheet(
            f"color: {THEME.on_surface_muted}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        mc_sub_desc.setWordWrap(True)
        box.addWidget(mc_sub_desc)

        mc_row = QHBoxLayout()
        mc_row.setSpacing(10)
        self.max_slider = M3Slider(Qt.Orientation.Horizontal, ignore_wheel=True)
        self.max_slider.setRange(self._normal_min, self._normal_max)
        self.max_slider.setSingleStep(25)
        self.max_slider.setValue(self._normal_max)
        self.max_slider.valueChanged.connect(self._on_max_slider)
        self.max_val = QLabel("Stock (unlocked)")
        self.max_val.setProperty("class", "ValueReadoutPill")
        self.max_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.max_val.setMinimumWidth(156)
        self.max_reset_btn = QPushButton("Reset")
        self.max_reset_btn.setProperty("class", "OutlinedButton")
        self.max_reset_btn.setIcon(icons.make_icon("undo", color=THEME.on_surface_variant, size=14))
        self.max_reset_btn.setIconSize(QSize(14, 14))
        self.max_reset_btn.setToolTip("Unlock the boost clock now")
        self.max_reset_btn.clicked.connect(lambda: self._reset_max())
        mc_row.addWidget(self.max_slider, 1)
        mc_row.addWidget(self.max_val)
        mc_row.addWidget(self.max_reset_btn)
        box.addLayout(mc_row)

        self.max_status_lbl = QLabel("")
        self.max_status_lbl.setStyleSheet(
            f"color: {THEME.success}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
        )
        box.addWidget(self.max_status_lbl)

        return card

    def _build_mem_clock_card(self) -> M3Card:
        card = M3Card()
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel("Memory Clock (VRAM)")
        title.setProperty("class", "CardTitle")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(
            M3IconHelpButton(
                "Independent memory frequency offset. Positive values overclock VRAM to increase "
                "memory bandwidth; negative values underclock; 0 is stock. The GPU driver "
                "automatically validates supported offsets."
            )
        )
        box.addLayout(hdr)

        self.mem_support_lbl = QLabel("")
        self.mem_support_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_overline}px; background: transparent;"
        )
        self.mem_support_lbl.setWordWrap(True)
        box.addWidget(self.mem_support_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.mem_slider = M3Slider(Qt.Orientation.Horizontal, ignore_wheel=True)
        self.mem_slider.setRange(dgpu_tune.MEM_OFFSET_MIN_MHZ, dgpu_tune.MEM_OFFSET_MAX_MHZ)
        self.mem_slider.setSingleStep(25)
        self.mem_slider.setPageStep(100)
        self.mem_slider.setValue(0)
        self.mem_slider.valueChanged.connect(self._on_mem_slider)
        self.mem_val = QLabel("+0 MHz")
        self.mem_val.setProperty("class", "ValueReadoutPill")
        self.mem_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mem_val.setMinimumWidth(120)
        self.mem_reset_btn = QPushButton("Reset")
        self.mem_reset_btn.setProperty("class", "OutlinedButton")
        self.mem_reset_btn.setIcon(icons.make_icon("undo", color=THEME.on_surface_variant, size=14))
        self.mem_reset_btn.setIconSize(QSize(14, 14))
        self.mem_reset_btn.setToolTip("Return the memory clock offset to stock now")
        self.mem_reset_btn.clicked.connect(lambda: self._reset_mem())
        row.addWidget(self.mem_slider, 1)
        row.addWidget(self.mem_val)
        row.addWidget(self.mem_reset_btn)
        box.addLayout(row)

        self.mem_status_lbl = QLabel("")
        self.mem_status_lbl.setStyleSheet(
            f"color: {THEME.success}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
        )
        box.addWidget(self.mem_status_lbl)
        return card

    def _build_apply_bar(self) -> QFrame:
        """The single commit point for all three controls."""
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background-color: {THEME.surface_container_high}; "
            f"border-radius: 16px; border: none; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(14)
        self.apply_btn = QPushButton("Apply Tuning")
        self.apply_btn.setProperty("class", "PrimaryButton")
        self.apply_btn.setIcon(
            icons.make_icon("check", color=THEME.on_accent_cta, size=16)
        )
        self.apply_btn.setIconSize(QSize(16, 16))
        self.apply_btn.setMinimumHeight(40)
        self.apply_btn.setToolTip("Apply the core offset, boost clock cap and memory offset together")
        self.apply_btn.clicked.connect(self._apply_all)
        row.addWidget(self.apply_btn)
        self.apply_hint_lbl = QLabel("")
        self.apply_hint_lbl.setStyleSheet(
            f"color: {THEME.on_surface_variant}; font-size: {THEME.text_overline}px; "
            f"font-weight: 500; background: transparent;"
        )
        self.apply_hint_lbl.setWordWrap(True)
        row.addWidget(self.apply_hint_lbl, 1)
        return bar

    # ── slider handlers ──
    def _on_uv_slider(self, value: int) -> None:
        self.uv_val.setText(f"+{value} MHz")
        self._sync_reset_buttons()
        self._update_apply_hint()

    def _on_max_slider(self, value: int) -> None:
        self._update_max_readout(value)
        self._sync_reset_buttons()
        self._update_apply_hint()

    def _on_mem_slider(self, _value: int) -> None:
        self._update_mem_readout()
        self._sync_reset_buttons()
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

    def _staged_controls(self) -> list[str]:
        staged: list[str] = []
        if self._uv_cfg_key is not None and self.uv_slider.value() != self._uv_cfg_key[1]:
            staged.append("undervolt")
        if self._max_cfg_key is not None:
            saved_en, saved_val = self._max_cfg_key
            cur_en = self.max_slider.value() < self._observed_ceiling
            cur_val = self.max_slider.value() if cur_en else 0
            if (cur_en, cur_val) != (bool(saved_en), int(saved_val) if saved_en else 0):
                staged.append("max clock")
        if self._mem_offset_cfg_key is not None and self._mem_offset() != self._mem_offset_cfg_key:
            staged.append("memory offset")
        return staged

    def _sync_reset_buttons(self) -> None:
        st = self._hw or {}
        uv_dirty_or_active = (
            (self.uv_slider.value() != 0)
            or bool(self._uv_cfg_key and self._uv_cfg_key[1] != 0)
            or (st.get("offset_mhz") not in (None, 0))
        )
        uv_can_reset = self._controls_enabled and not self._is_gated and uv_dirty_or_active
        self.uv_reset_btn.setEnabled(uv_can_reset)
        self.uv_reset_btn.setCursor(
            Qt.CursorShape.PointingHandCursor if uv_can_reset else Qt.CursorShape.ArrowCursor
        )
        self.uv_reset_btn.setToolTip(
            "Return the V/F offset to stock (0 MHz) now"
            if uv_can_reset
            else "Already at stock (0 MHz)"
        )

        max_dirty_or_active = (
            (self.max_slider.value() < self._observed_ceiling)
            or bool(self._max_cfg_key and self._max_cfg_key[0])
            or bool(st.get("applied_max"))
        )
        max_can_reset = self._controls_enabled and not self._is_gated and max_dirty_or_active
        self.max_reset_btn.setEnabled(max_can_reset)
        self.max_reset_btn.setCursor(
            Qt.CursorShape.PointingHandCursor if max_can_reset else Qt.CursorShape.ArrowCursor
        )
        self.max_reset_btn.setToolTip(
            "Unlock the boost clock now"
            if max_can_reset
            else "Already unlocked (stock)"
        )

        mem_dirty_or_active = (
            (self._mem_offset() != 0)
            or bool(self._mem_offset_cfg_key)
            or bool(st.get("applied_mem"))
        )
        mem_can_reset = self._mem_controls_enabled and not self._is_gated and mem_dirty_or_active
        self.mem_reset_btn.setEnabled(mem_can_reset)
        self.mem_reset_btn.setCursor(
            Qt.CursorShape.PointingHandCursor if mem_can_reset else Qt.CursorShape.ArrowCursor
        )
        self.mem_reset_btn.setToolTip(
            "Return the memory clock offset to stock now"
            if mem_can_reset
            else "Already at stock (0 MHz)"
        )

    def _update_apply_hint(self, gated: bool = False) -> None:
        """Tell the user when the sliders no longer match what is configured."""
        if gated:
            self.apply_hint_lbl.setStyleSheet(
                f"color: {THEME.gated}; font-size: {THEME.text_overline}px; "
                f"font-weight: 500; background: transparent;"
            )
            self.apply_hint_lbl.setText(
                "Tuning controls are read-only while dGPU is powered off (gated). "
                "Switch to Optimus to edit or apply."
            )
            self.apply_btn.setEnabled(False)
            self.apply_btn.setCursor(Qt.CursorShape.ArrowCursor)
            self.apply_btn.setToolTip("Read-only while dGPU is powered off (gated)")
            return

        staged = self._staged_controls()
        needs_manual_reapply = False
        cfg = load_config()
        st = self._hw or {}
        if not bool(cfg.get("dgpu_undervolt_auto", st.get("auto", True))) and self._gpu_awake:
            uv_cfg = int(cfg.get("dgpu_undervolt_offset_mhz", 0) or 0) if cfg.get("dgpu_undervolt_enabled", False) else 0
            max_cfg = int(cfg.get("dgpu_max_clock_mhz", 0) or 0) if cfg.get("dgpu_max_clock_enabled", False) else 0
            mem_cfg = int(cfg.get("dgpu_mem_offset_mhz", 0) or 0) if cfg.get("dgpu_mem_offset_enabled", False) else 0
            if (
                (uv_cfg and st.get("offset_mhz") != uv_cfg)
                or (max_cfg and st.get("applied_max") != max_cfg)
                or (mem_cfg and st.get("applied_mem") != mem_cfg)
            ):
                needs_manual_reapply = True

        can_apply = bool(self._controls_enabled and not gated and (staged or needs_manual_reapply))
        self.apply_btn.setEnabled(can_apply)
        self.apply_btn.setCursor(
            Qt.CursorShape.PointingHandCursor if can_apply else Qt.CursorShape.ArrowCursor
        )
        if staged:
            self.apply_btn.setToolTip("Apply staged tuning changes")
            self.apply_hint_lbl.setStyleSheet(
                f"color: {THEME.warning}; font-size: {THEME.text_overline}px; "
                f"font-weight: 600; background: transparent;"
            )
            self.apply_hint_lbl.setText(
                "Unapplied: " + ", ".join(staged) + " — press Apply to send them to the GPU.")
        else:
            self.apply_btn.setToolTip(
                "Re-apply saved tuning for this session"
                if needs_manual_reapply
                else "Sliders already match saved settings"
            )
            self.apply_hint_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: {THEME.text_overline}px; "
                f"font-weight: 500; background: transparent;"
            )
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

    def _refresh_holders(self, gpu) -> None:
        # The process/fd walk can take seconds. Never run it on the Qt thread,
        # or for a hidden page / sleeping GPU where its result is not displayed.
        if not self.isVisible() or gpu.vendor != "nvidia" or gpu.status != "active":
            self._holders = []
            self._holders_checked_at = None
            self.gpu_holders_lbl.setText("")
            self.gpu_holders_lbl.setVisible(False)
            return
        self.gpu_holders_lbl.setText(
            "Awake held by: " + ", ".join(self._holders) if self._holders else "")
        self.gpu_holders_lbl.setVisible(bool(self._holders))
        now = time.monotonic()
        if self._holders_busy or (
            self._holders_checked_at is not None
            and now - self._holders_checked_at < 10.0
        ):
            return
        self._holders_busy = True
        self._holders_checked_at = now
        results = self._holders_results
        scan = dgpu_tune.wake_holders

        def collect():
            # Capture only the queue/function; never retain or access Qt widgets
            # from the worker. A daemon worker also cannot delay GUI shutdown.
            try:
                holders = scan()
            except Exception:
                holders = []
            results.put(holders)

        threading.Thread(target=collect, name="gigamate-gpu-holders", daemon=True).start()
        self._holders_timer.start()

    def _collect_holders(self) -> None:
        try:
            holders = self._holders_results.get_nowait()
        except queue.Empty:
            return
        self._holders_busy = False
        self._holders_timer.stop()
        gpu = get_gpu_state()
        if self.isVisible() and gpu.vendor == "nvidia" and gpu.status == "active":
            self._holders = holders
            self._holders_checked_at = time.monotonic()
            self.gpu_holders_lbl.setText(
                "Awake held by: " + ", ".join(holders) if holders else "")
            self.gpu_holders_lbl.setVisible(bool(holders))
        else:
            self._holders = []
            self._holders_checked_at = None
            self.gpu_holders_lbl.setText("")
            self.gpu_holders_lbl.setVisible(False)

    def _on_gate_mode_clicked(self, mode: str) -> None:
        if self._gate_busy:
            return
        if mode == dgpu_gate.MODE_ECO and not dgpu_gate.eco_allowed_on_power():
            self._sync_power_gate_card(recheck_supported=False)
            return

        current_mode = str(load_config().get("dgpu_power_mode", dgpu_gate.MODE_OPTIMUS)).lower()
        if mode in ("eco", "auto") and not load_config().get("dgpu_gate_disclaimer_accepted", False):
            reply = QMessageBox.warning(
                self,
                "dGPU Power Gate",
                "dGPU Power Gate cuts the discrete GPU PCIe power rail using auto-discovered ACPI methods. "
                "HDMI/DisplayPort outputs wired to the dGPU stop working while gated, and firmware behavior "
                "varies by model — use at your own risk. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                for key, btn in self._gate_buttons.items():
                    btn.blockSignals(True)
                    btn.setChecked(key == current_mode)
                    btn.blockSignals(False)
                return
            update_config(lambda c: {**c, "dgpu_gate_disclaimer_accepted": True})
        gated_now = dgpu_gate.is_gated_off()
        try:
            policy = dgpu_gate.read_policy_state() or {}
        except Exception:  # noqa: BLE001
            policy = {}
        if mode == current_mode:
            if (mode == "optimus" and not gated_now) or (mode == "eco" and gated_now):
                if mode in self._gate_buttons:
                    self._gate_buttons[mode].setChecked(True)
                return
            if mode == "auto" and not policy.get("pending_off"):
                try:
                    ac_on = bool(get_battery_manager().is_ac_online())
                except Exception:  # noqa: BLE001
                    ac_on = True
                if gated_now or ac_on:
                    if mode in self._gate_buttons:
                        self._gate_buttons[mode].setChecked(True)
                    return
        needs_confirm = False
        try:
            if not dgpu_gate.is_gated_off():
                if mode == dgpu_gate.MODE_ECO:
                    needs_confirm = True
                elif mode == dgpu_gate.MODE_AUTO:
                    try:
                        ac_online = bool(get_battery_manager().is_ac_online())
                    except Exception:  # noqa: BLE001
                        ac_online = True
                    needs_confirm = not ac_online
        except Exception:  # noqa: BLE001
            needs_confirm = False

        if needs_confirm:
            from ..dgpu_gate_dialog import DgpuGateConfirmDialog

            try:
                prev_mode = str(load_config().get("dgpu_power_mode", dgpu_gate.MODE_OPTIMUS)).lower()
            except Exception:
                prev_mode = dgpu_gate.MODE_OPTIMUS
            dlg = DgpuGateConfirmDialog(
                trigger="eco" if mode == dgpu_gate.MODE_ECO else "auto",
                target_mode=mode,
                prev_mode=prev_mode,
                parent=self,
            )
            dlg.exec()
            self._refresh(recheck_gate=False)
            return

        self._gate_busy = True
        for btn in self._gate_buttons.values():
            btn.setEnabled(False)
        if hasattr(self, "gate_force_close_btn"):
            self.gate_force_close_btn.setEnabled(False)
        self.gate_status_lbl.setStyleSheet(
            f"color: {THEME.warning}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
        )
        self.gate_status_lbl.setText(f"Switching dGPU power mode to {mode}…")

        def _worker() -> None:
            closable = []
            try:
                res = dgpu_gate.set_mode(mode)
                if not res.get("ok") and res.get("reason") == "gpu_active":
                    closable = dgpu_gate.closable_gpu_holders()
            except Exception as exc:  # noqa: BLE001
                res = {"ok": False, "error": str(exc)}
            self._gate_results.put((mode, res, closable, True))

        threading.Thread(target=_worker, name="gigamate-gui-dgpu-gate", daemon=True).start()
        self._gate_timer.start()

    def _on_gate_force_close_clicked(self) -> None:
        if self._gate_busy:
            return
        from ..dgpu_gate_dialog import DgpuGateConfirmDialog

        try:
            cfg = load_config()
            mode = str(cfg.get("dgpu_power_mode", dgpu_gate.MODE_ECO)).lower()
            if mode not in (dgpu_gate.MODE_ECO, dgpu_gate.MODE_AUTO):
                mode = dgpu_gate.MODE_ECO
        except Exception:
            mode = dgpu_gate.MODE_ECO

        dlg = DgpuGateConfirmDialog(
            trigger="eco" if mode == dgpu_gate.MODE_ECO else "auto",
            target_mode=mode,
            prev_mode=mode,
            parent=self,
        )
        dlg.exec()
        self._refresh(recheck_gate=False)

    def _collect_gate_result(self) -> None:
        try:
            item = self._gate_results.get_nowait()
        except queue.Empty:
            return
        if len(item) == 4:
            mode, res, closable, prompt_allowed = item
        else:
            mode, res = item
            closable, prompt_allowed = [], False
        self._gate_busy = False
        self._controls_enabled: bool = False
        self._mem_controls_enabled: bool = False
        self._is_gated: bool = False
        self._gate_timer.stop()
        for btn in self._gate_buttons.values():
            btn.setEnabled(True)
        if hasattr(self, "gate_force_close_btn"):
            self.gate_force_close_btn.setEnabled(True)
        self._sync_power_gate_card(recheck_supported=False, override_result=(mode, res))
        self._refresh(recheck_gate=False)

        if (
            prompt_allowed
            and mode in (dgpu_gate.MODE_ECO, dgpu_gate.MODE_AUTO)
            and not res.get("ok")
            and res.get("reason") == "gpu_active"
            and closable
        ):
            from ..dgpu_gate_dialog import DgpuGateConfirmDialog

            dlg = DgpuGateConfirmDialog(
                trigger="eco" if mode == dgpu_gate.MODE_ECO else "auto",
                target_mode=mode,
                prev_mode=mode,
                parent=self,
            )
            dlg.exec()
            self._refresh(recheck_gate=False)

    def _sync_power_gate_card(
        self,
        recheck_supported: bool = False,
        override_result: Optional[tuple] = None,
    ) -> None:
        if not hasattr(self, "power_gate_card"):
            return
        if recheck_supported or self._gate_supported is None:
            try:
                self._gate_supported = bool(dgpu_gate.is_supported())
            except Exception:
                self._gate_supported = False

        self.power_gate_card.setVisible(bool(self._gate_supported))
        if not self._gate_supported:
            return

        self.gate_exp_badge.setText("Auto-discovered from ACPI")
        self.gate_exp_badge.setVisible(True)
        try:
            cfg = load_config()
            mode = str(cfg.get("dgpu_power_mode", dgpu_gate.MODE_OPTIMUS)).lower()
            if mode not in dgpu_gate.VALID_MODES:
                mode = dgpu_gate.MODE_OPTIMUS
        except Exception:
            mode = dgpu_gate.MODE_OPTIMUS

        if not self._gate_busy:
            for key, btn in self._gate_buttons.items():
                btn.blockSignals(True)
                btn.setChecked(key == mode)
                btn.blockSignals(False)

        if self._gate_busy:
            return

        gpu = get_gpu_state()
        gated = bool(gpu.gated_off or gpu.status == "gated_off")
        wedged = False
        if not gated:
            try:
                wedged = dgpu_gate.is_gpu_wedged()
            except Exception:  # noqa: BLE001
                wedged = False
        policy = {}
        try:
            policy = dgpu_gate.read_policy_state()
        except Exception:
            policy = {}

        try:
            ac_online = bool(get_battery_manager().is_ac_online())
        except Exception:  # noqa: BLE001
            ac_online = True
        eco_btn = self._gate_buttons.get(dgpu_gate.MODE_ECO)
        if isinstance(eco_btn, M3SegmentButton):
            eco_btn.setText("ECO (Battery only)" if ac_online else "ECO (dGPU Off)")
            eco_btn.setLocked(
                ac_online,
                tooltip=(
                    "ECO mode is only available on battery power"
                    if ac_online
                    else "Prompt & close GPU apps if needed, then cut power rail until switched back"
                ),
            )
        elif eco_btn is not None:
            eco_btn.setEnabled(not ac_online)
            eco_btn.setCursor(
                Qt.CursorShape.ForbiddenCursor if ac_online else Qt.CursorShape.PointingHandCursor
            )
            eco_btn.setText("ECO (Battery only)" if ac_online else "ECO (dGPU Off)")
            eco_btn.setToolTip(
                "ECO mode is only available on battery power"
                if ac_online
                else "Prompt & close GPU apps if needed, then cut power rail until switched back"
            )
        pwr_txt = "AC" if ac_online else "Battery"
        if gated:
            self.gate_rail_pill.set_tone("gated")
            self.gate_rail_pill.setText(f"Rail: OFF (~0 W) · {pwr_txt}")
        else:
            self.gate_rail_pill.set_tone("neutral")
            self.gate_rail_pill.setText(f"Rail: ON · {pwr_txt}")

        try:
            cfg_res = dgpu_gate.get_gate_config()
            eval_be = dgpu_gate.acpi_eval_backend()
            if cfg_res is not None:
                gc, _m = cfg_res
                self.gate_info_lbl.setText(
                    f"Target: {gc.path}.{gc.method} (via {eval_be})  ·  "
                    "Optimus: runtime D3cold  ·  ECO: rail off  ·  Auto: prompt on unplug, restore on AC"
                )
        except Exception:  # noqa: BLE001
            pass

        if override_result is not None:
            _m, res = override_result
            if not res.get("ok"):
                color = THEME.warning if res.get("reason") == "gpu_active" else THEME.error
                self.gate_status_lbl.setStyleSheet(
                    f"color: {color}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
                )
                self.gate_status_lbl.setText(str(res.get("error") or "Mode transition failed."))
                if hasattr(self, "gate_force_close_btn"):
                    self.gate_force_close_btn.setVisible(
                        res.get("reason") == "gpu_active" and not gated and mode in ("eco", "auto")
                    )
                return

        if hasattr(self, "gate_force_close_btn"):
            show_force = (
                not gated
                and mode in ("eco", "auto")
                and bool(policy.get("pending_off"))
                and policy.get("last_reason") in ("gpu_active", "awaiting_confirmation")
            )
            self.gate_force_close_btn.setVisible(show_force)

        if gated:
            self.gate_status_lbl.setStyleSheet("color: #38b2ac; font-size: 12px; font-weight: 600;")
            self.gate_status_lbl.setText("Off (gated) — dGPU power rail cut (~0 W).")
        elif wedged:
            self.gate_status_lbl.setStyleSheet("color: #fc8181; font-size: 12px; font-weight: 700;")
            self.gate_status_lbl.setText(
                "GPU needs a reboot — NVIDIA driver reset required; dGPU power gating paused."
            )
        elif policy.get("pending_off") and mode in ("eco", "auto"):
            self.gate_status_lbl.setStyleSheet(
            f"color: {THEME.warning}; font-size: {THEME.text_caption}px; font-weight: 500; background: transparent;"
        )
            msg = policy.get("last_error") or "Waiting for dGPU to suspend before cutting power."
            self.gate_status_lbl.setText(f"Pending ({mode}): {msg}")
        elif mode == "optimus":
            self.gate_status_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 500;")
            self.gate_status_lbl.setText("Optimus active — kernel runtime PM manages D3cold.")
        elif mode == "auto":
            self.gate_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px; font-weight: 500;")
            self.gate_status_lbl.setText("Auto active — AC connected; will prompt to power off on battery.")
        else:
            self.gate_status_lbl.setStyleSheet("color: #48bb78; font-size: 12px; font-weight: 500;")
            self.gate_status_lbl.setText("ECO active.")

    # ── refresh ──
    def _refresh(self, recheck_gate: bool = False) -> None:
        gpu = get_gpu_state()
        self._sync_power_gate_card(recheck_supported=recheck_gate)
        gated = bool(gpu.gated_off or gpu.status == "gated_off")
        wedged = False
        if not gated:
            try:
                wedged = dgpu_gate.is_gpu_wedged()
            except Exception:  # noqa: BLE001
                wedged = False
        # Free sysfs read; every NVML decision on this page keys off it.
        self._gpu_awake = bool(gpu.present and not gated and gpu.status == "active")
        if not gpu.present:
            self.gpu_state_lbl.setText("Not present (iGPU only)")
            self.gpu_state_lbl.setStyleSheet(
                f"color: {THEME.on_surface_muted}; font-size: {THEME.text_card_title}px; font-weight: 700; background: transparent;"
            )
            self.gpu_meta_lbl.setVisible(False)
            self.gpu_holders_lbl.setText("")
            self.gpu_holders_lbl.setVisible(False)
        elif gated:
            self.gpu_state_lbl.setText("Off (gated)")
            self.gpu_state_lbl.setStyleSheet(
                f"color: {THEME.gated}; font-size: {THEME.text_card_title}px; font-weight: 700; background: transparent;"
            )
            vendor_name = (gpu.vendor or "NVIDIA").upper()
            bdf = ""
            try:
                bdf = str(dgpu_gate.read_gate_state().get("bdf") or "")
            except Exception:
                bdf = ""
            meta = f"{vendor_name} · {bdf} removed (~0 W)" if bdf else f"{vendor_name} · PCI removed (~0 W)"
            self.gpu_meta_lbl.setText(meta)
            self.gpu_meta_lbl.setVisible(True)
            self.gpu_holders_lbl.setText("")
            self.gpu_holders_lbl.setVisible(False)
        elif wedged:
            self.gpu_state_lbl.setText("Needs reboot (wedged)")
            self.gpu_state_lbl.setStyleSheet(
                f"color: {THEME.error}; font-size: {THEME.text_card_title}px; font-weight: 700; background: transparent;"
            )
            vendor_name = (gpu.vendor or "NVIDIA").upper()
            bdf = str(self._hw.get("device") or "")
            meta = (
                f"{vendor_name} · {bdf} — driver reset required"
                if bdf
                else f"{vendor_name} — driver reset required"
            )
            self.gpu_meta_lbl.setText(meta)
            self.gpu_meta_lbl.setVisible(True)
            self.gpu_holders_lbl.setText("")
            self.gpu_holders_lbl.setVisible(False)
        else:
            self.gpu_state_lbl.setText(gpu_status_text(gpu))
            if gpu.status == "suspended":
                color = THEME.success
            elif gpu.status == "active":
                color = THEME.warning
            else:
                color = THEME.on_surface
            self.gpu_state_lbl.setStyleSheet(
                f"color: {color}; font-size: {THEME.text_card_title}px; font-weight: 700; background: transparent;"
            )
            vendor_name = (gpu.vendor or "Discrete").upper()
            bdf = str(self._hw.get("device") or "")
            self.gpu_meta_lbl.setText(f"{vendor_name} · {bdf}" if bdf else f"{vendor_name} GPU")
            self.gpu_meta_lbl.setVisible(True)
            self._refresh_holders(gpu)

        supported = False
        show_saved = False
        if gated:
            probed_for_display = self._probed or self._hw or {}
            show_saved = (gpu.vendor in (None, "nvidia"))
            self.uv_support_lbl.setStyleSheet(
                f"color: {THEME.gated}; font-size: {THEME.text_overline}px; font-weight: 500; background: transparent;"
            )
            self.uv_support_lbl.setText(
                "dGPU is powered off (gated) — your saved tuning profile is shown below and will "
                "automatically re-apply when the dGPU powers back on."
            )
        else:
            self.uv_support_lbl.setStyleSheet(
                f"color: {THEME.on_surface_variant}; font-size: {THEME.text_overline}px; background: transparent;"
            )
            # Cached-or-nothing probe: never spawns a helper while the dGPU sleeps.
            probed = self._probed
            try:
                probed = dgpu_tune.probe_if_awake() or probed
            except Exception:
                pass
            if probed:
                self._probed = probed

            # When the probe cache is cold (first open while dGPU is asleep), fall
            # back to the watcher's state file which already knows if tuning is
            # supported. This lets the UI show and edit the configured values without
            # touching NVML (which would wake the dGPU).
            if not probed:
                hw_supported = bool(self._hw.get("supported"))
                hw_device = self._hw.get("device", "NVIDIA GPU")
                supported = hw_supported and gpu.vendor == "nvidia"
                probed_for_display = {"supported": hw_supported, "device": hw_device}
            else:
                supported = bool(probed.get("supported"))
                probed_for_display = probed

            if gpu.vendor != "nvidia":
                self.uv_support_lbl.setText("Tuning is only available for NVIDIA dGPUs.")
                supported = False
            elif supported:
                device_name = probed_for_display.get("device") or "NVIDIA GPU"
                asleep_suffix = " · asleep (D3cold)" if not self._gpu_awake else ""
                self.uv_support_lbl.setText(f"Supported on {device_name}{asleep_suffix}")
            else:
                self.uv_support_lbl.setText(
                    "Not supported by this GPU/driver: " + str(probed_for_display.get("error") or "unavailable")
                )
            show_saved = supported

        controls_enabled = supported and not gated and not wedged
        self._is_gated = gated
        self._controls_enabled = controls_enabled
        self.uv_slider.setEnabled(controls_enabled)
        self.max_slider.setEnabled(controls_enabled)

        if show_saved:
            self._sync_uv_controls(gated=gated)
            self._sync_max_controls(gated=gated)
            self._sync_mem_controls(probed_for_display, gated=gated)
        else:
            self._mem_controls_enabled = False
            self.mem_slider.setEnabled(False)
            self.uv_status_lbl.setText("")
            self.max_status_lbl.setText("")
            self.mem_status_lbl.setText("")
        self._sync_reset_buttons()
        self._update_apply_hint(gated=gated)

    def _sync_uv_controls(self, gated: bool = False) -> None:
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
        auto = bool(cfg.get("dgpu_undervolt_auto", st.get("auto", True)))
        if gated:
            if enabled and offset > 0:
                if auto:
                    state = f"Saved: +{offset} MHz — auto-reapplies when dGPU powers on"
                else:
                    state = f"Saved: +{offset} MHz (auto-apply off)"
            else:
                state = "Off (stock)"
        elif enabled and offset > 0 and hw_off == offset:
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

    def _sync_max_controls(self, gated: bool = False) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_max_clock_enabled", False))
        wanted = int(cfg.get("dgpu_max_clock_mhz", 0) or 0)
        st = self._hw or {}
        applied_max = st.get("applied_max") or 0
        idle_cleared = bool(st.get("idle_cleared"))
        auto = bool(cfg.get("dgpu_undervolt_auto", st.get("auto", True)))

        if enabled and wanted > 0:
            # Widen the low end or upper end if a saved cap sits outside the probe range.
            eff_min = max(300, min(self._normal_min, wanted))
            eff_max = max(self._normal_max, wanted)
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
            if enabled and wanted > 0 and self._observed_ceiling <= wanted:
                self.max_val.setText(f"Cap {wanted} MHz")
            else:
                self._update_max_readout(self.max_slider.value())

        if gated:
            if enabled and wanted > 0:
                if auto:
                    state = f"Saved: cap {wanted} MHz — auto-reapplies when dGPU powers on"
                else:
                    state = f"Saved: cap {wanted} MHz (auto-apply off)"
            else:
                state = "Off (unlocked)"
        elif enabled and wanted > 0 and applied_max == wanted:
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

    def _sync_mem_controls(
        self,
        probed_for_display: Optional[dict] = None,
        gated: bool = False,
    ) -> None:
        cfg = load_config()
        enabled = bool(cfg.get("dgpu_mem_offset_enabled", False))
        wanted = int(cfg.get("dgpu_mem_offset_mhz", 0) or 0)
        st = self._hw or {}
        applied = st.get("applied_mem") or 0
        idle_cleared = bool(st.get("idle_cleared"))
        auto = bool(cfg.get("dgpu_undervolt_auto", st.get("auto", True)))
        # Read the helper version from the probe cache: asking for it must never
        # open NVML on a card the kernel has powered down. An unknown version is
        # not treated as stale — we simply have nothing to say.
        try:
            version = dgpu_tune.cached_helper_version()
        except Exception:
            version = None
        stale_helper = (version is not None
                        and version < dgpu_tune.REQUIRED_HELPER_VERSION)

        # When probe cache is cold (dGPU asleep, no NVML probe run yet), fall
        # back to the watcher state file which carries mem_offset_api from the
        # last time the GPU was awake.
        effective_probed = probed_for_display if probed_for_display is not None else self._probed
        offset_api = (
            bool(effective_probed.get("mem_offset_api"))
            or bool(st.get("mem_offset_api"))
        )
        # If neither probe nor state file have it yet, assume supported when the
        # watcher state explicitly says the GPU is supported (or when gated off with saved config).
        if not offset_api and (
            (bool(st.get("supported")) and (gated or not self._gpu_awake))
            or (gated and enabled and wanted != 0)
        ):
            offset_api = True

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

        self._mem_controls_enabled = bool(mem_supported and not gated)
        self.mem_slider.setEnabled(self._mem_controls_enabled)

        # Mirror the configured offset into the slider.
        target = wanted if enabled else 0
        if target != self._mem_offset_cfg_key:
            if not self.mem_slider.isSliderDown():
                self.mem_slider.blockSignals(True)
                self.mem_slider.setValue(target)
                self.mem_slider.blockSignals(False)
                self._update_mem_readout()
            self._mem_offset_cfg_key = target

        if not mem_supported and not (gated and enabled and wanted != 0):
            self.mem_status_lbl.setText("")
            return
        if gated:
            if enabled and wanted != 0:
                if auto:
                    state = f"Saved: {wanted:+d} MHz — auto-reapplies when dGPU powers on"
                else:
                    state = f"Saved: {wanted:+d} MHz (auto-apply off)"
            else:
                state = "Off (stock)"
        elif enabled and wanted != 0 and applied == wanted:
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

