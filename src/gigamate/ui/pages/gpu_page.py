"""GigaMate Center — dGPU Sleep Guard & Process Inspector Page."""

from typing import Optional
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...gpu_guard import DgpuSleepGuard, GpuGuardStatus, get_dgpu_guard


class GpuPage(QWidget):
    """dGPU Sleep Guard, zero-wake inspector, and process termination."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.guard = get_dgpu_guard()
        self._init_ui()

        # Update timer (every 2.5s)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_status)
        self.timer.start(2500)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)

        # ── Status Banner Card ──
        self.status_card = QFrame()
        self.status_card.setProperty("class", "Card")
        sc_layout = QVBoxLayout(self.status_card)
        sc_layout.setSpacing(8)

        self.status_title = QLabel("dGPU Power State: Inspecting...")
        self.status_title.setProperty("class", "CardTitle")
        self.status_title.setStyleSheet("font-size: 18px;")
        sc_layout.addWidget(self.status_title)

        self.status_sub = QLabel("Zero-wake sysfs monitoring active.")
        self.status_sub.setStyleSheet("color: #a0aec0; font-size: 13px;")
        sc_layout.addWidget(self.status_sub)
        layout.addWidget(self.status_card)

        # ── Actions Row ──
        actions_card = QFrame()
        actions_card.setProperty("class", "Card")
        ac_layout = QHBoxLayout(actions_card)
        ac_layout.setContentsMargins(16, 12, 16, 12)

        self.btn_kill_leeches = QPushButton("🛡️ Put dGPU to Sleep (Terminate Leeches)")
        self.btn_kill_leeches.setProperty("class", "DangerButton")
        self.btn_kill_leeches.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kill_leeches.clicked.connect(self._on_kill_leeches_clicked)
        ac_layout.addWidget(self.btn_kill_leeches)

        self.btn_refresh = QPushButton("🔄 Refresh Processes")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._refresh_status)
        ac_layout.addWidget(self.btn_refresh)

        layout.addWidget(actions_card)

        # ── Process Inspector Table Card ──
        table_card = QFrame()
        table_card.setProperty("class", "Card")
        tc_layout = QVBoxLayout(table_card)
        tc_layout.setSpacing(10)

        tc_title = QLabel("Processes Holding dGPU Resources")
        tc_title.setProperty("class", "CardTitle")
        tc_sub = QLabel(
            "Lists all applications with open handles to /dev/nvidia* or DRM dGPU render nodes. "
            "Session-critical processes are protected."
        )
        tc_sub.setProperty("class", "CardSubtitle")
        tc_layout.addWidget(tc_title)
        tc_layout.addWidget(tc_sub)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Process", "PID", "Category", "Open Devices", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(240)
        tc_layout.addWidget(self.table)

        layout.addWidget(table_card)
        layout.addStretch()

        self._refresh_status()

    def _refresh_status(self) -> None:
        """Inspect GPU state and populate process table."""
        status: GpuGuardStatus = self.guard.inspect(force_scan=True)

        if not status.present:
            self.status_title.setText("Discrete GPU: Not Present")
            self.status_title.setStyleSheet("color: #a0aec0;")
            self.status_sub.setText("System uses integrated graphics only.")
            self.table.setRowCount(0)
            self.btn_kill_leeches.setEnabled(False)
            return

        if not status.is_awake:
            self.status_title.setText(f"✓ dGPU is Asleep ({status.power_state or 'D3cold'})")
            self.status_title.setStyleSheet("color: #48bb78; font-weight: bold;")
            self.status_sub.setText("Consuming 0W. No wakeups detected (Zero-Wake Guard Active).")
            self.btn_kill_leeches.setEnabled(False)
        else:
            self.status_title.setText(f"⚠ dGPU is Awake ({status.power_state or 'D0'})")
            self.status_title.setStyleSheet("color: #ed8936; font-weight: bold;")
            leech_txt = f"{status.leech_count} background leech(es)" if status.leech_count else "0 leeches"
            self.status_sub.setText(f"Active power draw. Detected {len(status.processes)} processes ({leech_txt}).")
            self.btn_kill_leeches.setEnabled(status.leech_count > 0)

        # Update Table
        self.table.setRowCount(len(status.processes))
        for row, proc in enumerate(status.processes):
            # Process Name
            item_comm = QTableWidgetItem(proc.comm)
            item_comm.setFlags(item_comm.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item_comm)

            # PID
            item_pid = QTableWidgetItem(str(proc.pid))
            item_pid.setFlags(item_pid.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, item_pid)

            # Category
            if proc.is_protected:
                cat_text = "🔒 System Protected"
                cat_color = "#63b3ed"
            elif proc.is_leech:
                cat_text = "⚡ Background Leech"
                cat_color = "#f6ad55"
            else:
                cat_text = "App Client"
                cat_color = "#e2e8f0"

            item_cat = QTableWidgetItem(cat_text)
            item_cat.setForeground(Qt.GlobalColor.white)
            item_cat.setFlags(item_cat.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, item_cat)

            # Open Devices
            devs_str = ", ".join(proc.open_devices)
            item_devs = QTableWidgetItem(devs_str)
            item_devs.setFlags(item_devs.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, item_devs)

            # Action Button
            if proc.is_protected:
                locked_lbl = QLabel("Protected")
                locked_lbl.setStyleSheet("color: #718096; font-size: 11px; padding: 4px 8px;")
                locked_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setCellWidget(row, 4, locked_lbl)
            else:
                kill_btn = QPushButton("Terminate")
                kill_btn.setProperty("class", "DangerButton")
                kill_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
                kill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                kill_btn.clicked.connect(lambda _, p=proc.pid, n=proc.comm: self._on_kill_single_proc(p, n))
                self.table.setCellWidget(row, 4, kill_btn)

    def _on_kill_single_proc(self, pid: int, comm: str) -> None:
        res = QMessageBox.question(
            self,
            "Terminate Process",
            f"Are you sure you want to terminate '{comm}' (PID {pid}) to let the dGPU sleep?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            self.guard.terminate_process(pid)
            self._refresh_status()

    def _on_kill_leeches_clicked(self) -> None:
        res = QMessageBox.question(
            self,
            "Enforce dGPU Sleep",
            "This will terminate non-essential background processes (e.g. Steam, Discord, browsers) "
            "holding open the discrete GPU.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            results = self.guard.terminate_all_leeches()
            self.guard.request_gpu_sleep()
            self._refresh_status()
