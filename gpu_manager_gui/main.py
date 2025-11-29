from __future__ import annotations

import os
import sys
import time
import threading
import re
from typing import Optional, Dict, Any
import shlex
import subprocess

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QProgressBar,
    QHBoxLayout,
    QVBoxLayout,
    QAbstractItemView,
    QStackedWidget,
    QComboBox,
    QCheckBox,
    QToolButton,
    QSplitter,
    QTabBar,
)
from PyQt6.QtCharts import QChart, QChartView, QPieSeries
from PyQt6.QtWidgets import QTabWidget, QPlainTextEdit, QTableWidget, QTableWidgetItem, QPushButton, QDialog, QListWidget, QListWidgetItem

# Support both `python -m gpu_manager_gui.main` and direct script run
try:
    from .ssh_worker import SSHGpuPoller, Snapshot
    from .ssh_exec import SSHCommandJob, RemoteOSInfoJob, CondaEnvListJob, RemoteListDirJob, SSHInteractiveShell
    from .terminal_widget import TerminalWidget
    from . import config_store
except Exception:  # running as a script: fix sys.path and import absolutely
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from gpu_manager_gui.ssh_worker import SSHGpuPoller, Snapshot
    from gpu_manager_gui.ssh_exec import SSHCommandJob
    from gpu_manager_gui.ssh_exec import RemoteOSInfoJob
    from gpu_manager_gui.ssh_exec import CondaEnvListJob
    from gpu_manager_gui.ssh_exec import RemoteListDirJob
    from gpu_manager_gui.ssh_exec import SSHInteractiveShell
    from gpu_manager_gui.terminal_widget import TerminalWidget
    from gpu_manager_gui import config_store


class LoginPage(QWidget):
    # host, port, username, identity, password, interval
    connect_requested = pyqtSignal(str, int, object, object, object, float)
    test_requested = pyqtSignal(str, int, object, object, object, float)

    def __init__(self) -> None:
        super().__init__()
        box = QGroupBox("SSH Login")
        box.setMinimumWidth(420)
        box.setMaximumWidth(560)
        vbox = QVBoxLayout(box)

        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(False)
        self.profile_label = QLabel("Profile")
        profile_row = QHBoxLayout()
        profile_row.addWidget(self.profile_label)
        profile_row.addWidget(self.profile_combo, 1)
        vbox.addLayout(profile_row)

        # Single grid with two columns to keep perfect alignment
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # Required (left)
        self.host_edit = QLineEdit(); self.host_edit.setPlaceholderText("server or user@server")
        self.host_edit.setToolTip("Required: hostname or user@hostname")
        self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535); self.port_spin.setValue(22)
        self.port_spin.setToolTip("Required: SSH port (default 22)")
        self.interval_spin = QSpinBox(); self.interval_spin.setRange(1, 600); self.interval_spin.setValue(5)
        self.interval_spin.setToolTip("Required: polling interval in seconds")

        # Advanced (right)
        self.user_edit = QLineEdit(); self.user_edit.setPlaceholderText("username (optional)")
        self.user_edit.setToolTip("Optional: leave empty to use system ssh defaults or user@ in Host")
        self.ident_edit = QLineEdit(); self.ident_edit.setPlaceholderText("~/.ssh/id_rsa (optional)")
        self.ident_edit.setToolTip("Optional private key path; leave empty to use ssh-agent/keys")
        self.browse_btn = QPushButton("Browse…")
        ident_row = QHBoxLayout(); ident_row.addWidget(self.ident_edit, 1); ident_row.addWidget(self.browse_btn)
        ident_row_w = QWidget(); ident_row_w.setLayout(ident_row)
        self.pass_edit = QLineEdit(); self.pass_edit.setPlaceholderText("password (optional)")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_pass_cb = QCheckBox("Show")
        pass_row = QHBoxLayout(); pass_row.addWidget(self.pass_edit, 1); pass_row.addWidget(self.show_pass_cb)
        pass_row_w = QWidget(); pass_row_w.setLayout(pass_row)
        self.remember_cb = QCheckBox("Remember password (insecure)")
        self.remember_cb.setToolTip("Stores password base64 in YAML; not secure; for convenience only")
        self.auto_connect_cb = QCheckBox("Auto-connect last used on startup")

        # Uniform label widths across both columns
        fm = self.fontMetrics()
        labels = ["Profile", "Host *", "Port *", "Interval *", "User", "Identity", "Password"]
        label_w = max(fm.horizontalAdvance(s) for s in labels) + 8
        def L(text: str):
            lab = QLabel(text); lab.setMinimumWidth(label_w); return lab

        # Row 0: profile spans to the right
        grid.addWidget(L("Profile"), 0, 0)
        grid.addWidget(self.profile_combo, 0, 1, 1, 3)
        # Row 1
        grid.addWidget(L("Host *"), 1, 0); grid.addWidget(self.host_edit, 1, 1)
        grid.addWidget(L("User"), 1, 2); grid.addWidget(self.user_edit, 1, 3)
        # Row 2
        grid.addWidget(L("Port *"), 2, 0); grid.addWidget(self.port_spin, 2, 1)
        grid.addWidget(L("Identity"), 2, 2); grid.addWidget(ident_row_w, 2, 3)
        # Row 3
        grid.addWidget(L("Interval *"), 3, 0); grid.addWidget(self.interval_spin, 3, 1)
        grid.addWidget(L("Password"), 3, 2); grid.addWidget(pass_row_w, 3, 3)
        # Row 4+5 extra switches on the right
        grid.addWidget(L("") , 4, 2); grid.addWidget(self.remember_cb, 4, 3)
        grid.addWidget(L("") , 5, 2); grid.addWidget(self.auto_connect_cb, 5, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        vbox.addLayout(grid)

        # Footer
        self.req_note = QLabel("Fields marked * are required")
        self.req_note.setStyleSheet("color: gray;")
        vbox.addWidget(self.req_note)
        self.connect_btn = QPushButton("Connect")
        self.test_btn = QPushButton("Test")
        # Make buttons exactly the same size (height + min width)
        fm_btn = self.fontMetrics()
        btn_w = max(fm_btn.horizontalAdvance(self.test_btn.text()), fm_btn.horizontalAdvance(self.connect_btn.text())) + 32
        for b in (self.test_btn, self.connect_btn):
            b.setFixedHeight(36)
            b.setMinimumWidth(btn_w)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.test_btn)
        btn_row.addWidget(self.connect_btn)
        vbox.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(box, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

        self.browse_btn.clicked.connect(self._browse_identity)
        self.connect_btn.clicked.connect(self._emit_connect)
        self.profile_combo.currentTextChanged.connect(self._profile_changed)
        self.show_pass_cb.toggled.connect(self._toggle_password_echo)
        self.test_btn.clicked.connect(self._emit_test)

        # Apply basic styles for a cleaner look
        self._apply_styles()

    def _browse_identity(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SSH identity file", os.path.expanduser("~/.ssh"))
        if path:
            self.ident_edit.setText(path)

    def _emit_connect(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing host", "Please enter host as server or user@server")
            return
        port = int(self.port_spin.value())
        user = self.user_edit.text().strip() or None
        if user is None and "@" in host:
            maybe_user, maybe_host = host.split("@", 1)
            if maybe_user and maybe_host:
                user = maybe_user
                host = maybe_host
        ident = self.ident_edit.text().strip() or None
        password = self.pass_edit.text() or None
        interval = float(self.interval_spin.value())
        self.connect_requested.emit(host, port, user, ident, password, interval)

    def _profile_changed(self, key: str) -> None:
        # MainWindow handles filling; no-op to keep signal
        pass

    def _toggle_password_echo(self, checked: bool) -> None:
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _emit_test(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing host", "Please enter host as server or user@server")
            return
        port = int(self.port_spin.value())
        user = self.user_edit.text().strip() or None
        if user is None and "@" in host:
            maybe_user, maybe_host = host.split("@", 1)
            if maybe_user and maybe_host:
                user = maybe_user
                host = maybe_host
        ident = self.ident_edit.text().strip() or None
        password = self.pass_edit.text() or None
        interval = float(self.interval_spin.value())
        self.test_requested.emit(host, port, user, ident, password, interval)

    def _apply_styles(self) -> None:
        self.connect_btn.setObjectName("primaryButton")
        self.setStyleSheet(
            """
            QGroupBox { font-weight: 600; border: 1px solid #dcdce0; border-radius: 8px; margin-top: 12px; }
            /* Keep title in flow to avoid clipping on macOS */
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; background: transparent; }
            QLineEdit, QSpinBox, QComboBox { min-height: 28px; padding: 4px 6px; border: 1px solid #c9c9ce; border-radius: 6px; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #2d7ef7; }

            /* Button base */
            QPushButton { min-height: 36px; padding: 0 14px; border-radius: 6px; border: 1px solid #2d7ef7; color: #2d7ef7; background: #ffffff; }
            QPushButton:hover { background: #f0f6ff; }
            QPushButton:pressed { background: #dbe9ff; border: 1px solid #1e6de6; color: #1e6de6; }
            QPushButton:disabled { color: #9bb5ec; border: 1px solid #b7cbf5; background: #f5f8ff; }
            QPushButton:checked { background: #eaf2ff; border: 1px solid #2d7ef7; color: #1e6de6; }

            /* Primary buttons */
            QPushButton#primaryButton { background: #2d7ef7; color: white; border: 1px solid #2d7ef7; }
            QPushButton#primaryButton:hover { background: #3a86f8; }
            QPushButton#primaryButton:pressed { background: #1e6de6; border: 1px solid #1e6de6; }
            QPushButton#primaryButton:disabled { background: #9dbcf7; color: white; border: 1px solid #9dbcf7; }

            QLabel { color: #222; }
            """
        )

    # Populate helpers
    def set_profiles(self, keys: list[str]) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for k in keys:
            self.profile_combo.addItem(k)
        self.profile_combo.blockSignals(False)

    def fill_from_profile(self, prof: Dict[str, Any]) -> None:
        self.host_edit.setText(str(prof.get("host", "")))
        self.port_spin.setValue(int(prof.get("port", 22)))
        self.user_edit.setText(str(prof.get("username", "")))
        self.ident_edit.setText(str(prof.get("identity", "")))
        self.interval_spin.setValue(int(float(prof.get("interval", 5))))
        pw = prof.get("password") or ""
        self.pass_edit.setText(pw)
        self.remember_cb.setChecked(bool(prof.get("remember_password", False)))


class TopTabs(QWidget):
    """Left-aligned top tab bar + stacked pages (workaround for centered QTabWidget on macOS)."""
    def __init__(self) -> None:
        super().__init__()
        self._bar = QTabBar(movable=False)
        self._bar.setExpanding(False)  # do not stretch; keep tabs compact
        self._stack = QStackedWidget()
        v = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(self._bar); top.addStretch(1)
        v.addLayout(top)
        v.addWidget(self._stack, 1)
        self._bar.currentChanged.connect(self._stack.setCurrentIndex)

    def addTab(self, w: QWidget, title: str) -> None:
        idx = self._stack.addWidget(w)
        self._bar.addTab(title)
        if self._bar.count() == 1:
            self._bar.setCurrentIndex(0)
            self._stack.setCurrentIndex(0)

    def widget(self) -> QWidget:
        return self


class MonitorPage(QWidget):
    disconnect_requested = pyqtSignal()
    # Signals to bubble actions to MainWindow (works even if parent chain changes)
    docker_refresh_req = pyqtSignal()
    conda_refresh_req = pyqtSignal()
    preview_update_req = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        # Back-reference to MainWindow (set by MainWindow after construction)
        self._mw = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.os_label = QLabel("")
        # Allow selecting/copying the OS text on PyQt6
        try:
            self.os_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        except Exception:
            # Fallback if enum not found for some reason
            self.os_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.disconnect_btn = QPushButton("Disconnect")
        top.addWidget(self.os_label)
        top.addStretch(1)
        top.addWidget(self.disconnect_btn)
        # Note: header added into Monitor tab to keep tabs at window top

        center = QWidget()
        hbox = QHBoxLayout(center)
        self.gpu_table = QTableWidget(0, 5)
        self.gpu_table.setHorizontalHeaderLabels(["GPU", "Name", "Util %", "Memory", "Procs"])
        self.gpu_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.gpu_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.gpu_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.gpu_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.gpu_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.gpu_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.gpu_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Top processes table (PID, User, Mem MiB, GPU, Name)
        self.proc_table = QTableWidget(0, 5)
        self.proc_table.setHorizontalHeaderLabels(["PID", "User", "Mem (MiB)", "GPU", "Name"])
        self.proc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.proc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.proc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Left vertical splitter: GPUs (top) and Top Processes (bottom)
        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.addWidget(self.gpu_table)
        left_split.addWidget(self.proc_table)
        left_split.setStretchFactor(0, 3)
        left_split.setStretchFactor(1, 2)
        self.chart = QChart()
        self.chart.setTitle("Per-user VRAM (MiB)")
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        hbox.addWidget(left_split, 3)
        hbox.addWidget(self.chart_view, 2)
        self.disconnect_btn.clicked.connect(lambda: self.disconnect_requested.emit())

        # Runner panel container (no titled box)
        runner_box = QWidget()
        r_v = QVBoxLayout(runner_box)
        try:
            r_v.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass

        # Top form: use a grid so labels align nicely
        self.script_edit = QLineEdit(); self.script_edit.setPlaceholderText("/path/to/train.py or play.py")
        self.script_browse = QPushButton("Browse")
        self.conda_combo = QComboBox(); self.conda_combo.setEditable(True); self.conda_combo.setMinimumWidth(220)
        self.conda_refresh = QPushButton("Refresh")
        top_form = QGridLayout(); top_form.setHorizontalSpacing(12); top_form.setVerticalSpacing(6)
        # Row 0: Presets (moved to the very top as requested)
        self.preset_combo = QComboBox(); self.preset_combo.setEditable(True); self.preset_combo.setMinimumWidth(180)
        self.preset_save = QPushButton("Save")
        self.preset_load = QPushButton("Load")
        self.preset_del = QPushButton("Delete")
        prow = QHBoxLayout(); prow.addWidget(self.preset_combo, 1); prow.addWidget(self.preset_save); prow.addWidget(self.preset_load); prow.addWidget(self.preset_del)
        prow_w = QWidget(); prow_w.setLayout(prow)
        top_form.addWidget(QLabel("Presets"), 0, 0)
        top_form.addWidget(prow_w, 0, 1)
        # Row 1: Script
        top_form.addWidget(QLabel("Script"), 1, 0)
        srow = QHBoxLayout(); srow.addWidget(self.script_edit, 1); srow.addWidget(self.script_browse)
        srow_w = QWidget(); srow_w.setLayout(srow)
        top_form.addWidget(srow_w, 1, 1)
        # Row 2: Conda / Docker
        top_form.addWidget(QLabel("Conda / Docker"), 2, 0)
        self.use_docker_cb = QCheckBox("Docker")
        self.docker_combo = QComboBox(); self.docker_combo.setEditable(False); self.docker_combo.setMinimumWidth(200)
        self.docker_refresh = QPushButton("Refresh containers")
        self.use_compose_cb = QCheckBox("Compose")
        self.compose_dir_edit = QLineEdit(); self.compose_dir_edit.setPlaceholderText("/path/to/compose dir (e.g. ~/PycharmProjects/.../docker)")
        self.compose_service_edit = QLineEdit(); self.compose_service_edit.setPlaceholderText("service name (e.g. isaac-lab-nhb)")
        crow = QHBoxLayout();
        crow.addWidget(QLabel("Conda")); crow.addWidget(self.conda_combo, 1); crow.addWidget(self.conda_refresh)
        crow.addSpacing(12)
        crow.addWidget(self.use_docker_cb); crow.addWidget(self.docker_combo, 1); crow.addWidget(self.docker_refresh)
        crow.addSpacing(12)
        crow.addWidget(self.use_compose_cb); crow.addWidget(self.compose_dir_edit, 1); crow.addWidget(self.compose_service_edit, 1)
        crow_w = QWidget(); crow_w.setLayout(crow)
        top_form.addWidget(crow_w, 2, 1)
        top_form.setColumnStretch(1, 1)
        r_v.addLayout(top_form)

        form_row = QHBoxLayout()
        left_form = QFormLayout(); right_form = QFormLayout()
        # (script moved to top row)
        # Params table (--key=value)
        self.params_table = QTableWidget(0, 2); self.params_table.setHorizontalHeaderLabels(["param", "value"])
        self.params_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.params_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.params_add = QPushButton("+ param"); self.params_del = QPushButton("- param")
        self.params_table.setAlternatingRowColors(True)
        left_form.addRow(self.params_table)
        left_btns = QHBoxLayout(); left_btns.addWidget(self.params_add); left_btns.addWidget(self.params_del); left_btns.addStretch(1)
        left_form.addRow(left_btns)
        # Env table (KEY=VALUE)
        self.env_table = QTableWidget(0, 2); self.env_table.setHorizontalHeaderLabels(["env", "value"])
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.env_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.env_add = QPushButton("+ env"); self.env_del = QPushButton("- env")
        self.env_table.setAlternatingRowColors(True)
        right_form.addRow(self.env_table)
        right_btns = QHBoxLayout(); right_btns.addWidget(self.env_add); right_btns.addWidget(self.env_del); right_btns.addStretch(1)
        right_form.addRow(right_btns)
        # Put the two forms into a splitter so user can resize
        lr_split = QSplitter(Qt.Orientation.Horizontal)
        lw = QWidget(); lw.setLayout(left_form)
        rw = QWidget(); rw.setLayout(right_form)
        lr_split.addWidget(lw); lr_split.addWidget(rw)
        lr_split.setStretchFactor(0, 1); lr_split.setStretchFactor(1, 1)
        r_v.addWidget(lr_split, 1)

        # Preview + Run
        # Preview header + per-command list with copy buttons
        self.run_btn = QPushButton("Run")
        try:
            self.run_btn.setObjectName("primaryButton")
        except Exception:
            pass
        ph = QHBoxLayout(); ph.addWidget(QLabel("Preview")); ph.addStretch(1); ph.addWidget(self.run_btn)
        r_v.addLayout(ph)
        self.preview_area = QWidget(); self.preview_vbox = QVBoxLayout(self.preview_area)
        try:
            self.preview_vbox.setContentsMargins(0, 0, 0, 0)
            self.preview_vbox.setSpacing(0)
        except Exception:
            pass
        r_v.addWidget(self.preview_area)

        # Tabs: Monitor vs Runner (top-level main tabs, left-aligned)
        self.main_tabs = TopTabs()
        monitor_tab = QWidget(); mt_l = QVBoxLayout(monitor_tab); mt_l.addLayout(top); mt_l.addWidget(center, 1)
        runner_tab = QWidget(); rt_l = QVBoxLayout(runner_tab)
        rt_l.addWidget(runner_box, 1)
        # Console tab (interactive shell)
        console_tab = QWidget(); ct_l = QVBoxLayout(console_tab)
        cons_ctrl = QHBoxLayout()
        self.console_open_btn = QPushButton("Open Host Shell")
        self.console_compose_btn = QPushButton("Compose Shell")
        self.console_close_btn = QPushButton("Close")
        self.console_clear_btn = QPushButton("Clear")
        cons_ctrl.addWidget(self.console_open_btn); cons_ctrl.addWidget(self.console_compose_btn)
        cons_ctrl.addStretch(1); cons_ctrl.addWidget(self.console_clear_btn); cons_ctrl.addWidget(self.console_close_btn)
        ct_l.addLayout(cons_ctrl)
        self.terminal = TerminalWidget()
        ct_l.addWidget(self.terminal, 1)

        self.main_tabs.addTab(monitor_tab, "Monitor")
        self.main_tabs.addTab(runner_tab, "Runner")
        self.main_tabs.addTab(console_tab, "Console")
        layout.addWidget(self.main_tabs.widget(), 1)

        # Apply 1/3 : 2/3 column ratios and wire runner buttons
        try:
            self.params_table.installEventFilter(self)
            self.env_table.installEventFilter(self)
        except Exception:
            pass
        self._apply_column_ratio(self.params_table, 1.0/3.0)
        self._apply_column_ratio(self.env_table, 1.0/3.0)
        # Wire runner buttons
        self.params_add.clicked.connect(lambda: self._add_row(self.params_table))
        self.params_del.clicked.connect(lambda: self._del_selected(self.params_table))
        self.env_add.clicked.connect(lambda: self._add_row(self.env_table))
        self.env_del.clicked.connect(lambda: self._del_selected(self.env_table))
        # Run click is wired in MainWindow to ensure lifecycle
        # refresh handling bound in MainWindow to ensure lifecycle
        self.script_browse.clicked.connect(self._on_browse_script)
        # Populate per-command preview on demand (MainWindow drives updates)
        # Docker toggle wiring
        self.use_docker_cb.toggled.connect(self._on_docker_toggle)
        self.docker_combo.currentTextChanged.connect(lambda _=None: self.preview_update_req.emit())
        self.conda_combo.currentTextChanged.connect(lambda _=None: self.preview_update_req.emit())
        self.conda_refresh.clicked.connect(self._on_refresh_conda)
        # Hide legacy docker-only refresh; unified by single Refresh button
        try:
            self.docker_refresh.hide()
        except Exception:
            pass
        # Compose wiring
        self.use_compose_cb.toggled.connect(lambda _=None: self.preview_update_req.emit())
        self.compose_dir_edit.textChanged.connect(lambda _=None: self.preview_update_req.emit())
        self.compose_service_edit.textChanged.connect(lambda _=None: self.preview_update_req.emit())
        # Console wiring (delegated to MainWindow)
        try:
            self.console_open_btn.clicked.connect(lambda: getattr(self._mw, '_open_console_shell')() if getattr(self, '_mw', None) and hasattr(self._mw, '_open_console_shell') else None)
            self.console_compose_btn.clicked.connect(lambda: getattr(self._mw, '_open_compose_shell')() if getattr(self, '_mw', None) and hasattr(self._mw, '_open_compose_shell') else None)
            self.console_close_btn.clicked.connect(lambda: getattr(self._mw, '_close_console_shell')() if getattr(self, '_mw', None) and hasattr(self._mw, '_close_console_shell') else None)
            self.console_clear_btn.clicked.connect(self.terminal.clear)
        except Exception:
            pass
        # Preset buttons are wired in MainWindow for lifecycle

    def _on_refresh_conda(self) -> None:
        # Delegate to MainWindow to trigger detection
        try:
            use_docker = bool(self.use_docker_cb.isChecked())
        except Exception:
            use_docker = False
        try:
            sys.stdout.write(f"[ui] refresh (from MonitorPage) mode={'docker' if use_docker else 'conda'}\n"); sys.stdout.flush()
        except Exception:
            pass
        # Prefer direct call to MainWindow if available to avoid signal wiring issues
        if getattr(self, '_mw', None) is not None:
            try:
                if use_docker and hasattr(self._mw, '_detect_remote_docker_containers'):
                    self._mw._detect_remote_docker_containers(True)
                    return
                if (not use_docker) and hasattr(self._mw, '_detect_remote_conda_envs'):
                    self._mw._detect_remote_conda_envs(True)
                    return
            except Exception:
                pass
        # Fallback to signals
        if use_docker:
            self.docker_refresh_req.emit()
        else:
            self.conda_refresh_req.emit()

    def _on_browse_script(self) -> None:
        # Ask MainWindow to open remote file dialog, since it holds SSH params
        p = self.parent()
        if p and hasattr(p, "_browse_remote_script"):
            getattr(p, "_browse_remote_script")()

    # Previously we had a Show/Hide Log toggle here. Now we keep the output log
    # visible by default and let users adjust its size via the splitter handle.

    def _on_docker_toggle(self, checked: bool) -> None:
        # Enable/disable conda widgets when docker is selected
        try:
            self.conda_combo.setEnabled(not checked)
            # Keep unified Refresh button enabled in both modes
            self.conda_refresh.setEnabled(True)
            self.docker_combo.setEnabled(checked)
            # Legacy docker_refresh hidden in unified mode; ignore enable
            # Compose behaves independently; no auto-disable here
        except Exception:
            pass
        # Auto refresh container list when toggled on
        try:
            if checked:
                try:
                    sys.stdout.write("[ui] docker toggled on\n"); sys.stdout.flush()
                except Exception:
                    pass
                # emit signal to MainWindow
                self.docker_refresh_req.emit()
        except Exception:
            pass
        # ask MainWindow to rebuild preview
        self.preview_update_req.emit()

    def _on_refresh_docker(self) -> None:
        try:
            sys.stdout.write("[ui] Docker refresh button clicked\n"); sys.stdout.flush()
        except Exception:
            pass
        # Prefer direct call to MainWindow if available
        if getattr(self, '_mw', None) is not None and hasattr(self._mw, '_detect_remote_docker_containers'):
            try:
                self._mw._detect_remote_docker_containers(True)
                return
            except Exception:
                pass
        # Fallback: Emit signal; MainWindow will handle
        self.docker_refresh_req.emit()

    def _notify_parent_update_preview(self) -> None:
        # Backward-compat helper; now just emit signal
        self.preview_update_req.emit()

    # Keep first column at 1/3 width, second at 2/3
    def _apply_column_ratio(self, table: QTableWidget, r_first: float = 1.0/3.0) -> None:
        try:
            total = max(0, table.viewport().width())
            c0 = int(total * max(0.05, min(0.95, r_first)))
            c1 = max(0, total - c0)
            table.setColumnWidth(0, c0)
            table.setColumnWidth(1, c1)
        except Exception:
            pass

    def eventFilter(self, obj, ev):  # type: ignore[override]
        try:
            if ev.type() == QEvent.Type.Resize:
                if obj is self.params_table:
                    self._apply_column_ratio(self.params_table, 1.0/3.0)
                elif obj is self.env_table:
                    self._apply_column_ratio(self.env_table, 1.0/3.0)
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def set_preview_commands(self, cmds: list[str]) -> None:
        """Render per-command preview rows with a copy button for each."""
        try:
            layout = self.preview_vbox
        except Exception:
            return
        # Clear previous rows (widgets and spacers)
        try:
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
            try:
                layout.setSpacing(0)
            except Exception:
                pass
        except Exception:
            pass
        # Add one row per command
        for cmd in (cmds or []):
            row = QHBoxLayout()
            try:
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(0)
            except Exception:
                pass
            le = QLineEdit(); le.setReadOnly(True); le.setText(cmd)
            btn = QPushButton("复制")
            try:
                btn.clicked.connect(lambda _=False, t=cmd: QApplication.clipboard().setText(t))
            except Exception:
                pass
            row.addWidget(le, 1)
            row.addWidget(btn)
            w = QWidget(); w.setLayout(row)
            layout.addWidget(w)
        # No trailing stretch; keep lines tight

    def _add_row(self, table: QTableWidget) -> None:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(""))
        table.setItem(row, 1, QTableWidgetItem(""))

    def _del_selected(self, table: QTableWidget) -> None:
        for idx in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(idx)

    def update_snapshot(self, snap: 'Snapshot') -> None:
        rows = len(snap.gpus)
        self.gpu_table.setRowCount(rows)
        procs_per_uuid = {}
        for app in snap.apps:
            procs_per_uuid[app.gpu_uuid] = procs_per_uuid.get(app.gpu_uuid, 0) + 1
        for r, g in enumerate(snap.gpus):
            idx_item = QTableWidgetItem(str(g.index))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 0, idx_item)
            name_item = QTableWidgetItem(g.name)
            self.gpu_table.setItem(r, 1, name_item)
            util_item = QTableWidgetItem(f"{g.util_percent}%")
            util_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 2, util_item)
            prog = QProgressBar()
            prog.setRange(0, max(1, g.mem_total_mib))
            prog.setValue(g.mem_used_mib)
            prog.setFormat(f"{g.mem_used_mib} / {g.mem_total_mib} MiB")
            self.gpu_table.setCellWidget(r, 3, prog)
            n_procs = procs_per_uuid.get(g.uuid, 0)
            procs_item = QTableWidgetItem(str(n_procs))
            procs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 4, procs_item)

        # Build pie based on TOTAL VRAM across all GPUs, not just used-by-users
        base_total = sum(max(0, g.mem_total_mib) for g in snap.gpus)
        used_total = sum(max(0, g.mem_used_mib) for g in snap.gpus)
        user_totals = dict(snap.user_vram_mib)
        used_by_users = sum(max(0, v) for v in user_totals.values())

        # System/other = driver/reserved/video memory not attributed to a user
        system_other = max(0.0, float(used_total) - float(used_by_users))
        free_rest = max(0.0, float(base_total) - float(used_total))

        series = QPieSeries()
        series.setLabelsVisible(True)

        # Users first (sorted desc)
        if user_totals:
            for user, mib in sorted(user_totals.items(), key=lambda kv: kv[1], reverse=True):
                val = max(0.01, float(mib))
                series.append(f"{user} ({int(mib)} MiB)", val)
        # Then system/other (only if non-zero)
        if system_other > 0.5:
            series.append(f"system/other ({int(system_other)} MiB)", system_other)
        # Finally free rest to ensure the whole circle equals total VRAM
        if base_total <= 0:
            # No GPUs? show idle placeholder
            series.append("idle", 1)
        elif free_rest > 0.5:
            series.append(f"free ({int(free_rest)} MiB)", free_rest)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("VRAM Total = users + system + free (MiB)")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self.chart_view.setChart(chart)

        # Build Top-10 processes by VRAM usage with owners
        # Map GPU uuid -> index for display
        uuid_to_idx = {g.uuid: g.index for g in snap.gpus}
        pid_user = getattr(snap, 'pid_user_map', {}) or {}
        rows = []
        for a in snap.apps:
            rows.append((a.used_memory_mib, a.pid, pid_user.get(a.pid, 'unknown'), uuid_to_idx.get(a.gpu_uuid, -1), a.process_name))
        rows.sort(key=lambda r: r[0], reverse=True)
        top = rows[:10]
        self.proc_table.setRowCount(len(top))
        for r, (mib, pid, user, gpu_idx, name) in enumerate(top):
            self.proc_table.setItem(r, 0, QTableWidgetItem(str(pid)))
            self.proc_table.setItem(r, 1, QTableWidgetItem(user))
            self.proc_table.setItem(r, 2, QTableWidgetItem(str(mib)))
            self.proc_table.setItem(r, 3, QTableWidgetItem(str(gpu_idx if gpu_idx >= 0 else ''))) 
            self.proc_table.setItem(r, 4, QTableWidgetItem(name))


class RemoteFileDialog(QDialog):
    def __init__(self, host_params: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse Remote Files")
        self.resize(700, 520)
        self._hp = host_params
        self._cwd = ""
        self._selected: Optional[str] = None

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.path_edit = QLineEdit(); self.path_edit.setReadOnly(True)
        self.up_btn = QPushButton("Up")
        self.home_btn = QPushButton("Home")
        top.addWidget(QLabel("Path")); top.addWidget(self.path_edit, 1); top.addWidget(self.up_btn); top.addWidget(self.home_btn)
        v.addLayout(top)

        self.list = QListWidget(); v.addWidget(self.list, 1)
        btns = QHBoxLayout(); btns.addStretch(1)
        self.sel_btn = QPushButton("Select"); self.cancel_btn = QPushButton("Cancel")
        btns.addWidget(self.sel_btn); btns.addWidget(self.cancel_btn)
        v.addLayout(btns)

        self.up_btn.clicked.connect(self._go_up)
        self.home_btn.clicked.connect(lambda: self._list_dir(""))
        self.sel_btn.clicked.connect(self._select_current)
        self.cancel_btn.clicked.connect(self.reject)
        self.list.itemDoubleClicked.connect(self._on_double)

        self._list_dir("")

    def selected_path(self) -> str:
        return self._selected or ""

    def _go_up(self) -> None:
        p = (self._cwd or "/").rstrip("/")
        if not p:
            return
        parent = p.rsplit("/", 1)[0]
        if not parent:
            parent = "/"
        self._list_dir(parent)

    def _on_double(self, item: QListWidgetItem) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        name = item.text()
        if t == 'D':
            path = (self._cwd.rstrip("/") + "/" + name) if self._cwd else name
            self._list_dir(path)
        elif t == 'F':
            # Accept only .py files
            if name.lower().endswith('.py'):
                self._selected = (self._cwd.rstrip("/") + "/" + name) if self._cwd else name
                self.accept()

    def _select_current(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        t = it.data(Qt.ItemDataRole.UserRole)
        name = it.text()
        if t == 'D':
            self._on_double(it)
        else:
            if name.lower().endswith('.py'):
                self._selected = (self._cwd.rstrip("/") + "/" + name) if self._cwd else name
                self.accept()

    def _list_dir(self, path: str) -> None:
        try:
            job = RemoteListDirJob(self._hp["host"], int(self._hp["port"]), self._hp.get("username"), self._hp.get("identity"), self._hp.get("password"), path)
        except Exception:
            return
        def _res(cwd: str, entries: list) -> None:
            self._cwd = cwd
            self.path_edit.setText(cwd)
            self.list.clear()
            # Show dirs first then files
            for t in ('D','F','O'):
                for e in entries:
                    if e.get('type') != t:
                        continue
                    name = str(e.get('name',''))
                    # Only show .py files; always show directories for navigation
                    if t == 'F' and not name.lower().endswith('.py'):
                        continue
                    it = QListWidgetItem(name)
                    it.setData(Qt.ItemDataRole.UserRole, t)
                    self.list.addItem(it)
        def _err(m: str) -> None:
            QMessageBox.warning(self, "Remote browse", m or "list failed")
        job.result.connect(_res)
        job.error.connect(_err)
        job.setParent(self)
        job.start()

    def _add_row(self, table: QTableWidget) -> None:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(""))
        table.setItem(row, 1, QTableWidgetItem(""))

    def _del_selected(self, table: QTableWidget) -> None:
        for idx in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(idx)

    def update_snapshot(self, snap: 'Snapshot') -> None:
        rows = len(snap.gpus)
        self.gpu_table.setRowCount(rows)
        procs_per_uuid = {}
        for app in snap.apps:
            procs_per_uuid[app.gpu_uuid] = procs_per_uuid.get(app.gpu_uuid, 0) + 1
        for r, g in enumerate(snap.gpus):
            idx_item = QTableWidgetItem(str(g.index))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 0, idx_item)
            name_item = QTableWidgetItem(g.name)
            self.gpu_table.setItem(r, 1, name_item)
            util_item = QTableWidgetItem(f"{g.util_percent}%")
            util_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 2, util_item)
            prog = QProgressBar()
            prog.setRange(0, max(1, g.mem_total_mib))
            prog.setValue(g.mem_used_mib)
            prog.setFormat(f"{g.mem_used_mib} / {g.mem_total_mib} MiB")
            self.gpu_table.setCellWidget(r, 3, prog)
            n_procs = procs_per_uuid.get(g.uuid, 0)
            procs_item = QTableWidgetItem(str(n_procs))
            procs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 4, procs_item)

        # Build pie based on TOTAL VRAM across all GPUs, not just used-by-users
        base_total = sum(max(0, g.mem_total_mib) for g in snap.gpus)
        used_total = sum(max(0, g.mem_used_mib) for g in snap.gpus)
        user_totals = dict(snap.user_vram_mib)
        used_by_users = sum(max(0, v) for v in user_totals.values())

        # System/other = driver/reserved/video memory not attributed to a user
        system_other = max(0.0, float(used_total) - float(used_by_users))
        free_rest = max(0.0, float(base_total) - float(used_total))

        series = QPieSeries()
        series.setLabelsVisible(True)

        # Users first (sorted desc)
        if user_totals:
            for user, mib in sorted(user_totals.items(), key=lambda kv: kv[1], reverse=True):
                val = max(0.01, float(mib))
                series.append(f"{user} ({int(mib)} MiB)", val)
        # Then system/other (only if non-zero)
        if system_other > 0.5:
            series.append(f"system/other ({int(system_other)} MiB)", system_other)
        # Finally free rest to ensure the whole circle equals total VRAM
        if base_total <= 0:
            # No GPUs? show idle placeholder
            series.append("idle", 1)
        elif free_rest > 0.5:
            series.append(f"free ({int(free_rest)} MiB)", free_rest)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("VRAM Total = users + system + free (MiB)")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self.chart_view.setChart(chart)

    # Runner wiring helpers ----------------------------------------------
    def get_mode(self) -> str:
        # Mode tabs removed; always return single default mode
        return "default"



class ConnectTester(QThread):
    finished_ok = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], ssh_bin: str = "ssh", timeout: float = 8.0) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._username = username
        self._identity = identity
        self._password = password
        self._ssh_bin = ssh_bin
        self._timeout = float(timeout)

    def run(self) -> None:  # type: ignore[override]
        if self._password:
            try:
                import paramiko
            except Exception:
                self.failed.emit("Please pip install paramiko to use password auth")
                return
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self._host,
                    port=self._port,
                    username=self._username,
                    password=self._password,
                    key_filename=self._identity,
                    timeout=self._timeout,
                    banner_timeout=max(self._timeout, 10.0),
                    auth_timeout=max(self._timeout, 10.0),
                    allow_agent=True,
                    look_for_keys=True,
                )
                _, stdout, _ = client.exec_command("bash -lc 'nvidia-smi -L || nvidia-smi --query-gpu=index --format=csv,noheader,nounits'", timeout=self._timeout)
                out = stdout.read().decode(errors='ignore').strip()
                client.close()
                if out:
                    self.finished_ok.emit()
                else:
                    self.failed.emit("Connected but nvidia-smi returned empty output")
            except Exception as e:
                msg = str(e)
                if "Error reading SSH protocol banner" in msg:
                    msg += "; check host/port/firewall or increase banner timeout"
                self.failed.emit(msg)
            return
        cmd = [self._ssh_bin, "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        dest = f"{self._username}@{self._host}" if self._username else self._host
        cmd += [dest, "--", "bash", "-lc", "nvidia-smi -L || nvidia-smi --query-gpu=index --format=csv,noheader,nounits"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=self._timeout)
            if p.returncode == 0 and p.stdout.strip():
                self.finished_ok.emit()
            else:
                msg = p.stderr.strip() or p.stdout.strip() or f"ssh failed rc={p.returncode}"
                self.failed.emit(msg)
        except subprocess.TimeoutExpired:
            self.failed.emit("ssh connect timed out")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IsaacLab GPU Manager")
        self.resize(1100, 650)

        self._poller: Optional[SSHGpuPoller] = None
        self._cur_host: Optional[str] = None
        self._config: Dict[str, Any] = config_store.load_config()
        self._test_threads: list[ConnectTester] = []
        self._bg_jobs: list[QThread] = []
        self._host_params: Dict[str, Any] = {}
        # Console session holder
        self._console_shell = None

        # Pages
        self.stack = QStackedWidget()
        self.login_page = LoginPage()
        self.monitor_page = MonitorPage()
        # Set back-reference so MonitorPage can call into MainWindow reliably
        try:
            self.monitor_page._mw = self
        except Exception:
            pass
        self.stack.addWidget(self.login_page)
        # Show top-level tabs (Monitor/Runner) as the connected page
        self.stack.addWidget(self.monitor_page.main_tabs)
        self.setCentralWidget(self.stack)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # Wiring
        self.login_page.connect_requested.connect(self._begin_connect)
        self.login_page.test_requested.connect(self._test_connect)
        self.monitor_page.disconnect_requested.connect(self._disconnect)
        # Provide back-reference for Console actions
        try:
            self.monitor_page._mw = self
        except Exception:
            pass
        # MonitorPage signals (robust across parent changes)
        try:
            self.monitor_page.docker_refresh_req.connect(lambda: self._detect_remote_docker_containers(True))
            self.monitor_page.conda_refresh_req.connect(lambda: self._detect_remote_conda_envs(True))
            self.monitor_page.preview_update_req.connect(self._update_runner_preview)
        except Exception:
            pass
        self.login_page.profile_combo.currentTextChanged.connect(self._load_profile_into_fields)
        # Runner actions
        self.monitor_page.run_btn.clicked.connect(self._run_runner)
        self.monitor_page.conda_combo.currentTextChanged.connect(lambda _=None: self._update_runner_preview())
        try:
            self.monitor_page.docker_combo.currentTextChanged.connect(lambda _=None: self._update_runner_preview())
            self.monitor_page.use_docker_cb.toggled.connect(lambda _=None: self._update_runner_preview())
        except Exception:
            pass
        self.monitor_page.script_edit.textChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.params_table.itemChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.env_table.itemChanged.connect(lambda _=None: self._update_runner_preview())
        # Mode tabs removed; no mode change signal
        # Refresh tooling: conda or docker depending on toggle
        self.monitor_page.conda_refresh.clicked.connect(lambda: self._refresh_runner_envs(True))
        # unified Refresh handles docker/conda; no separate docker_refresh click binding
        # Preset actions
        try:
            self.monitor_page.preset_save.clicked.connect(self._save_preset)
            self.monitor_page.preset_load.clicked.connect(self._load_preset_into_ui)
            self.monitor_page.preset_del.clicked.connect(self._delete_preset)
        except Exception:
            pass

        # Load profiles
        self._refresh_profiles()
        # Load preset names once
        try:
            self._refresh_presets()
        except Exception:
            pass
        last_key = self._config.get("last_used_key")
        # Reflect auto-connect toggle state on UI
        if hasattr(self.login_page, 'auto_connect_cb'):
            self.login_page.auto_connect_cb.setChecked(bool(self._config.get("auto_connect_last_used", False)))
        if last_key and last_key in self._config.get("profiles", {}):
            self.login_page.profile_combo.setCurrentText(last_key)
            self._load_profile_into_fields(last_key)
        # Warn if PyYAML missing (profiles won't persist)
        if not config_store.yaml_available():
            QMessageBox.warning(self, "Profiles not persisted", "PyYAML is not installed; connection profiles won't be saved.\nRun: pip install PyYAML")
        # Auto connect last used profile if enabled
        if self._config.get("auto_connect_last_used") and last_key and last_key in self._config.get("profiles", {}):
            prof = dict(self._config["profiles"][last_key])
            host = prof.get("host", "")
            port = int(prof.get("port", 22))
            user = prof.get("username") or None
            ident = prof.get("identity") or None
            pw = config_store.get_profile_password(prof)
            interval = float(prof.get("interval", 5))
            if host:
                self._begin_connect(host, port, user, ident, pw, interval)

    def _refresh_profiles(self) -> None:
        keys = sorted(list(self._config.get("profiles", {}).keys()))
        self.login_page.set_profiles(keys)

    def _load_profile_into_fields(self, key: str) -> None:
        prof = self._config.get("profiles", {}).get(key)
        if not prof:
            return
        prof2 = dict(prof)
        prof2["password"] = config_store.get_profile_password(prof)
        self.login_page.fill_from_profile(prof2)

    def closeEvent(self, event):  # type: ignore[override]
        if self._poller is not None:
            self._poller.stop()
            self._poller.requestInterruption()
            self._poller.wait(500)
            self._poller = None
        return super().closeEvent(event)

    def _begin_connect(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], interval: float) -> None:
        self.status.showMessage("Testing SSH connection…")
        self.login_page.connect_btn.setEnabled(False)
        self._pending_params = (host, port, username, identity, password, interval)
        self._tester = ConnectTester(host, port, username, identity, password)
        self._tester.finished_ok.connect(self._on_connect_ok)
        self._tester.failed.connect(self._on_connect_failed)
        self._tester.finished.connect(lambda: self.login_page.connect_btn.setEnabled(True))
        self._tester.start()

    def _on_connect_ok(self) -> None:
        host, port, user, ident, password, interval = self._pending_params  # type: ignore[attr-defined]
        self._poller = SSHGpuPoller(host=host, port=port, username=user, password=password, identity_file=ident, interval_sec=interval)
        self._poller.snapshot_ready.connect(self._on_snapshot)
        self._poller.error_msg.connect(self._on_error)
        self._poller.finished.connect(self._on_poller_finished)
        self._poller.start()
        self._cur_host = host
        self._host_params = {"host": host, "port": port, "username": user, "identity": ident, "password": password}
        self.stack.setCurrentIndex(1)
        self.setWindowTitle(f"IsaacLab GPU Manager — {host}")
        self.status.showMessage("Connected. Polling every %.0f s" % interval)
        # Save profile on success
        remember = self.login_page.remember_cb.isChecked()
        self._config["auto_connect_last_used"] = bool(getattr(self.login_page, 'auto_connect_cb', None) and self.login_page.auto_connect_cb.isChecked())
        prof = dict(host=host, port=port, username=user or "", identity=ident or "", interval=interval, remember_password=remember)
        config_store.save_profile(self._config, prof, password if remember else None)
        config_store.save_config(self._config)
        self.status.showMessage(f"Saved profile to {config_store.config_path()}")
        self._refresh_profiles()
        self.login_page.profile_combo.setCurrentText(config_store.make_key(host, port, user))
        # Load runner config & detect conda envs
        self._load_runner_config()
        self._detect_remote_conda_envs(False)
        try:
            self._detect_remote_docker_containers(False)
        except Exception:
            pass
        self._fetch_remote_os_info()

    def _test_connect(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], interval: float) -> None:
        self.status.showMessage("Testing SSH connection…")
        self.login_page.test_btn.setEnabled(False)
        tester = ConnectTester(host, port, username, identity, password)
        tester.setParent(self)  # ensure Qt keeps it alive
        self._test_threads.append(tester)

        def _cleanup():
            try:
                self._test_threads.remove(tester)
            except ValueError:
                pass
            tester.deleteLater()
            self.login_page.test_btn.setEnabled(True)

        tester.finished_ok.connect(lambda: QMessageBox.information(self, "Test", "Connection OK"))
        tester.failed.connect(lambda m: QMessageBox.critical(self, "Test failed", m or "Unknown error"))
        tester.finished.connect(_cleanup)
        tester.start()

    def _on_connect_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "SSH connect failed", msg or "Unknown error")
        self.status.showMessage("Connect failed")

    def _disconnect(self) -> None:
        if self._poller is not None:
            self._poller.stop()
            self._poller.requestInterruption()
            self._poller.wait(1000)
            self._poller = None
        self.stack.setCurrentIndex(0)
        self.setWindowTitle("IsaacLab GPU Manager")
        self.status.showMessage("Disconnected")
        self._host_params = {}

    # Slots from worker
    def _on_snapshot(self, snap: Snapshot) -> None:
        try:
            if hasattr(self.monitor_page, 'update_snapshot'):
                self.monitor_page.update_snapshot(snap)
            else:
                self._update_snapshot_fallback(snap)
        except Exception as e:
            try:
                self._log_debug(f"[ui:error] update_snapshot failed: {e}")
            except Exception:
                pass
        self.status.showMessage(
            f"Last update: {time.strftime('%H:%M:%S')} | GPUs: {len(snap.gpus)} | users: {len(snap.user_vram_mib)}"
        )
        # Update preview each snapshot in case fields changed
        self._update_runner_preview()

    def _on_error(self, msg: str) -> None:
        if msg:
            self.status.showMessage(msg, 5000)

    def _log_debug(self, text: str) -> None:
        try:
            self.monitor_page.terminal.local_echo(text.rstrip("\n"))
        except Exception:
            pass

    def _on_poller_finished(self) -> None:
        self._poller = None
        if self.stack.currentIndex() == 1:
            self.stack.setCurrentIndex(0)
            self.status.showMessage("Disconnected")

    # Console shell helpers -----------------------------------------------
    def _open_console_shell(self) -> None:
        if self._console_shell is not None:
            self.status.showMessage("Console already open", 3000)
            return
        hp = self._host_params
        if not hp:
            QMessageBox.warning(self, "Not connected", "Please connect first")
            return
        try:
            shell = SSHInteractiveShell(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"), strip_ansi=False)
        except Exception as e:
            QMessageBox.critical(self, "Console", str(e) or "failed to create shell")
            return
        self._console_shell = shell
        self.monitor_page.terminal.local_echo("[console] opening host shell…")
        try:
            self.monitor_page.terminal.attach_shell(shell)
        except Exception:
            pass
        shell.data.connect(self.monitor_page.terminal.feed)
        shell.error.connect(lambda m: self.monitor_page.terminal.local_echo(f"[console:error] {m}"))
        shell.connected.connect(lambda: self.status.showMessage("Console connected", 3000))
        try:
            shell.connected.connect(lambda: self.monitor_page.terminal.send_resize())
        except Exception:
            pass
        def _closed():
            self.status.showMessage("Console closed", 3000)
            self._console_shell = None
            try:
                self.monitor_page.terminal.detach_shell()
            except Exception:
                pass
        shell.closed.connect(_closed)
        shell.start()
        # Focus console tab
        try:
            self.monitor_page.main_tabs._bar.setCurrentIndex(2)  # TopTabs bar index
        except Exception:
            pass
        try:
            self.monitor_page.terminal.setFocus()
        except Exception:
            pass

    def _send_console_line(self, text: str) -> None:
        if not text:
            return
        sh = self._console_shell
        if sh is None:
            self.status.showMessage("Console not open", 3000)
            return
        try:
            sh.send_line(text)
        except Exception as e:
            try:
                self.monitor_page.terminal.local_echo(f"[console:error] send failed: {e}")
            except Exception:
                pass

    def _open_compose_shell(self) -> None:
        # Ensure console is open
        if self._console_shell is None:
            self._open_console_shell()
            # Will run compose after connected; simple delay
            QThread.msleep(200)
        r = self._collect_runner()
        if not r.get("use_compose"):
            self.status.showMessage("Compose not enabled; fill dir/service and toggle Compose", 5000)
        compose_dir = (r.get("compose_dir") or "").strip()
        compose_service = (r.get("compose_service") or "").strip()
        if not compose_dir or not compose_service:
            QMessageBox.warning(self, "Compose", "Please fill Compose dir and service in Runner")
            return
        cmd = f"cd {compose_dir} && docker compose exec {compose_service} bash -l"
        try:
            self.monitor_page.terminal.local_echo(f"[console] {cmd}")
        except Exception:
            pass
        self._send_console_line(cmd)

    def _close_console_shell(self) -> None:
        sh = self._console_shell
        if sh is None:
            self.status.showMessage("Console already closed", 3000)
            return
        try:
            sh.stop_shell()
        except Exception:
            pass
        try:
            self.monitor_page.terminal.detach_shell()
        except Exception:
            pass

    # Fallback UI update if MonitorPage lacks update_snapshot (defensive)
    def _update_snapshot_fallback(self, snap: Snapshot) -> None:
        mp = self.monitor_page
        # Table update
        rows = len(snap.gpus)
        mp.gpu_table.setRowCount(rows)
        procs_per_uuid = {}
        for app in snap.apps:
            procs_per_uuid[app.gpu_uuid] = procs_per_uuid.get(app.gpu_uuid, 0) + 1
        for r, g in enumerate(snap.gpus):
            idx_item = QTableWidgetItem(str(g.index))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            mp.gpu_table.setItem(r, 0, idx_item)
            name_item = QTableWidgetItem(g.name)
            mp.gpu_table.setItem(r, 1, name_item)
            util_item = QTableWidgetItem(f"{g.util_percent}%")
            util_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            mp.gpu_table.setItem(r, 2, util_item)
            prog = QProgressBar()
            prog.setRange(0, max(1, g.mem_total_mib))
            prog.setValue(g.mem_used_mib)
            prog.setFormat(f"{g.mem_used_mib} / {g.mem_total_mib} MiB")
            mp.gpu_table.setCellWidget(r, 3, prog)
            n_procs = procs_per_uuid.get(g.uuid, 0)
            procs_item = QTableWidgetItem(str(n_procs))
            procs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            mp.gpu_table.setItem(r, 4, procs_item)
        # Pie
        base_total = sum(max(0, g.mem_total_mib) for g in snap.gpus)
        used_total = sum(max(0, g.mem_used_mib) for g in snap.gpus)
        user_totals = dict(snap.user_vram_mib)
        used_by_users = sum(max(0, v) for v in user_totals.values())
        system_other = max(0.0, float(used_total) - float(used_by_users))
        free_rest = max(0.0, float(base_total) - float(used_total))
        series = QPieSeries(); series.setLabelsVisible(True)
        if user_totals:
            for user, mib in sorted(user_totals.items(), key=lambda kv: kv[1], reverse=True):
                val = max(0.01, float(mib)); series.append(f"{user} ({int(mib)} MiB)", val)
        if system_other > 0.5:
            series.append(f"system/other ({int(system_other)} MiB)", system_other)
        if base_total <= 0:
            series.append("idle", 1)
        elif free_rest > 0.5:
            series.append(f"free ({int(free_rest)} MiB)", free_rest)
        chart = QChart(); chart.addSeries(series); chart.setTitle("VRAM Total = users + system + free (MiB)")
        chart.legend().setVisible(True); chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        mp.chart_view.setChart(chart)

    def _fetch_remote_os_info(self) -> None:
        hp = self._host_params
        # Log activity to the console terminal for visibility
        try:
            self.monitor_page.terminal.local_echo(
                "[osinfo] fetching at %s" % time.strftime('%H:%M:%S')
            )
        except Exception:
            pass
        if not hp:
            # Not connected: inform user visibly and return
            try:
                self.monitor_page.terminal.local_echo("[osinfo] Not connected")
                self.status.showMessage("Not connected", 5000)
            except Exception:
                pass
            return
        try:
            job = RemoteOSInfoJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"))
        except Exception:
            return
        def _set(text: str) -> None:
            try:
                self.monitor_page.os_label.setText(text)
            except Exception:
                pass
        job.result.connect(_set)
        job.error.connect(lambda m: _set(f"OS: unknown | {m}"))
        job.setParent(self)
        self._bg_jobs.append(job)
        job.finished.connect(lambda: self._bg_jobs.remove(job) if job in self._bg_jobs else None)
        job.start()

    # Unified refresh for conda/docker ------------------------------------
    def _refresh_runner_envs(self, from_click: bool = False) -> None:
        try:
            use_docker = bool(getattr(self.monitor_page, 'use_docker_cb', None) and self.monitor_page.use_docker_cb.isChecked())
        except Exception:
            use_docker = False
        try:
            sys.stdout.write(f"[ui] refresh mode={'docker' if use_docker else 'conda'}\n"); sys.stdout.flush()
        except Exception:
            pass
        if use_docker:
            self._detect_remote_docker_containers(from_click)
        else:
            self._detect_remote_conda_envs(from_click)

    # Runner command build/run -------------------------------------------
    def _collect_runner(self) -> Dict[str, Any]:
        # Single-mode runner
        mode = 'default'
        conda_env = self.monitor_page.conda_combo.currentText().strip()
        use_docker = bool(self.monitor_page.use_docker_cb.isChecked())
        docker_container = self.monitor_page.docker_combo.currentText().strip()
        use_compose = bool(getattr(self.monitor_page, 'use_compose_cb', None) and self.monitor_page.use_compose_cb.isChecked())
        compose_dir = self.monitor_page.compose_dir_edit.text().strip() if hasattr(self.monitor_page, 'compose_dir_edit') else ""
        compose_service = self.monitor_page.compose_service_edit.text().strip() if hasattr(self.monitor_page, 'compose_service_edit') else ""
        script = self.monitor_page.script_edit.text().strip()
        # params
        params = []
        t = self.monitor_page.params_table
        for r in range(t.rowCount()):
            k_item = t.item(r, 0); v_item = t.item(r, 1)
            k = (k_item.text() if k_item else "").strip()
            v = (v_item.text() if v_item else "").strip()
            if k:
                params.append([k, v])
        # env
        env = []
        e = self.monitor_page.env_table
        for r in range(e.rowCount()):
            k_item = e.item(r, 0); v_item = e.item(r, 1)
            k = (k_item.text() if k_item else "").strip()
            v = (v_item.text() if v_item else "").strip()
            if k:
                env.append([k, v])
        return {
            "mode": mode,
            "conda_env": conda_env,
            "use_docker": use_docker,
            "docker_container": docker_container,
            "use_compose": use_compose,
            "compose_dir": compose_dir,
            "compose_service": compose_service,
            "script": script,
            "params": params,
            "env": env,
        }

    def _build_python_cmd(self, runner: Dict[str, Any]) -> str:
        script = runner.get("script") or ""
        parts = ["python", shlex.quote(script)] if script else ["python"]
        for k, v in runner.get("params", []):
            key = str(k).strip()
            if not key:
                continue
            if not key.startswith("--"):
                key = "--" + key
            if v is None or v == "":
                parts.append(key)
            else:
                parts.append(f"{key}={shlex.quote(str(v))}")
        return " ".join(parts)

    def _update_runner_preview(self) -> None:
        """Render multi-stage shell commands (no comments) with per-command copy UI."""
        try:
            r = self._collect_runner()
            cmds = self._build_preview_commands(r)
            if hasattr(self.monitor_page, 'set_preview_commands'):
                self.monitor_page.set_preview_commands(cmds)
            else:
                # Fallback: show as concatenated text if legacy widget present
                try:
                    self.monitor_page.preview_edit.setPlainText("\n".join(cmds))
                except Exception:
                    pass
        except Exception as e:
            try:
                self._log_debug(f"[ui:error] preview failed: {e}")
            except Exception:
                pass

    def _build_preview_commands(self, r: Dict[str, Any]) -> list[str]:
        """Return ordered shell commands without comments for the preview list."""
        import shlex as _sh
        env_dict = {k: v for k, v in r.get("env", [])}
        use_docker = bool(r.get("use_docker"))
        docker_container = (r.get("docker_container") or "").strip() if use_docker else ""
        use_compose = bool(r.get("use_compose"))
        compose_dir = (r.get("compose_dir") or "").strip() if use_compose else ""
        compose_service = (r.get("compose_service") or "").strip() if use_compose else ""
        # container vs conda: mutually exclusive
        conda_env = "" if (use_docker or use_compose) else (r.get("conda_env") or "").strip()

        base_py = self._build_python_cmd(r)

        def _env_exports_lines() -> list[str]:
            return [f"export {k}={_sh.quote(str(v))}" for k, v in env_dict.items()]

        def _conda_lines() -> list[str]:
            if not conda_env:
                return []
            return [
                "for p in \"$HOME/miniconda3/etc/profile.d/conda.sh\" \"$HOME/anaconda3/etc/profile.d/conda.sh\" /opt/conda/etc/profile.d/conda.sh; do [ -f \"$p\" ] && . \"$p\" && break; done",
                f"conda activate {_sh.quote(conda_env)}",
            ]

        cmds: list[str] = []
        if use_compose and compose_dir and compose_service:
            cmds += [
                f"cd {_sh.quote(compose_dir)}",
                f"docker compose exec {_sh.quote(compose_service)} bash -l",
            ]
            cmds += _env_exports_lines()
            cmds.append(base_py)
            return cmds

        if use_docker and docker_container:
            cmds += [f"docker exec -it {_sh.quote(docker_container)} bash -l"]
            cmds += _env_exports_lines()
            cmds.append(base_py)
            return cmds

        if use_docker and not docker_container:
            cmds += ["docker exec -it <container> bash -l"]
            cmds += _env_exports_lines()
            cmds.append(base_py)
            return cmds

        # host conda or system python
        cmds += _conda_lines()
        cmds += _env_exports_lines()
        cmds.append(base_py)
        return cmds

    def _run_runner(self) -> None:
        hp = self._host_params
        if not hp:
            QMessageBox.warning(self, "Not connected", "Please connect first")
            return
        r = self._collect_runner()
        # Save config per host+mode
        key = self._host_key()
        if key:
            config_store.save_runner(self._config, key, r["mode"], r)
        env_dict = {k: v for k, v in r.get("env", [])}
        base = self._build_python_cmd(r)
        use_docker = bool(r.get("use_docker"))
        docker_container = (r.get("docker_container") or "").strip() if use_docker else ""
        use_compose = bool(r.get("use_compose"))
        compose_dir = (r.get("compose_dir") or "").strip() if use_compose else ""
        compose_service = (r.get("compose_service") or "").strip() if use_compose else ""

        if use_compose and compose_dir and compose_service:
            # Compose 模式：在宿主机 cd 到目录，再进入服务 shell 执行命令
            import shlex as _sh
            inner_in_container = SSHCommandJob.build_inner(env=env_dict, conda_env=None, base_cmd=base, docker_container=None)
            inner = f"cd {_sh.quote(compose_dir)} && docker compose exec {_sh.quote(compose_service)} bash -l -c {_sh.quote(inner_in_container)}"
        elif use_docker and docker_container:
            # Docker 模式：通过 docker exec 在容器内执行（不使用 conda）
            inner = SSHCommandJob.build_inner(env=env_dict, conda_env=None, base_cmd=base, docker_container=docker_container)
        else:
            # 主机模式：可使用 conda
            if use_docker and not docker_container:
                try:
                    self.monitor_page.terminal.local_echo("[run] docker 已勾选但未选择容器，将在主机上运行")
                except Exception:
                    pass
            inner = SSHCommandJob.build_inner(env=env_dict, conda_env=(r.get("conda_env") or None), base_cmd=base)

        job = SSHCommandJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"), inner)
        try:
            self.monitor_page.terminal.local_echo("[run] starting job…")
        except Exception:
            pass
        self.monitor_page.run_btn.setEnabled(False)
        job.line.connect(lambda s: self.monitor_page.terminal.local_echo(s.rstrip("\n")))
        job.error.connect(lambda m: self.monitor_page.terminal.local_echo(f"[error] {m}"))
        def _done(rc: int) -> None:
            try:
                self.monitor_page.terminal.local_echo(f"\n[exit] rc={rc}")
            except Exception:
                pass
            self.monitor_page.run_btn.setEnabled(True)
        job.finished.connect(_done)
        job.setParent(self)
        job.start()

    # Runner helpers ------------------------------------------------------
    def _host_key(self) -> Optional[str]:
        hp = self._host_params
        if not hp:
            return None
        return config_store.runner_key(hp["host"], int(hp["port"]), hp.get("username"))

    def _load_runner_config(self) -> None:
        key = self._host_key()
        if not key:
            return
        mode = 'default'
        r = config_store.load_runner(self._config, key, mode)
        self._apply_runner_fields(r)

    def _detect_remote_conda_envs(self, from_click: bool = False) -> None:
        # CondaEnvListJob is imported at module level with robust fallback for script/module runs
        hp = self._host_params
        if from_click:
            try:
                self.monitor_page.terminal.local_echo(
                    "[ui] Refresh envs clicked at %s" % time.strftime('%H:%M:%S')
                )
                self.monitor_page.terminal.local_echo(
                    "[conda-detect] host=%s user=%s port=%s" % (
                        hp.get('host'), hp.get('username'), hp.get('port')
                    )
                )
            except Exception:
                pass
        if not hp:
            if from_click:
                try:
                    self.monitor_page.terminal.local_echo("[ui] Not connected; cannot refresh envs")
                    self.status.showMessage("Not connected", 5000)
                except Exception:
                    pass
            return
        if from_click:
            try:
                self.monitor_page.conda_refresh.setEnabled(False)
                try:
                    self.monitor_page.conda_refresh.setText("Refreshing…")
                except Exception:
                    pass
            except Exception:
                pass
            self.status.showMessage("Refreshing remote conda environments…")
            try:
                sys.stdout.write("[ui] Refresh envs clicked; host=%s user=%s port=%s\n" % (hp.get('host'), hp.get('username'), hp.get('port')))
                sys.stdout.flush()
            except Exception:
                pass
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            except Exception:
                pass
        job = CondaEnvListJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"))
        job.result.connect(self._on_conda_envs)
        def _on_error(m: str) -> None:
            try:
                self.monitor_page.terminal.local_echo(("[conda-detect:error] " + (m or "")).rstrip("\n"))
                self.status.showMessage(m or "conda refresh failed", 5000)
            finally:
                if from_click:
                    try:
                        self.monitor_page.conda_refresh.setEnabled(True)
                    except Exception:
                        pass
        job.error.connect(_on_error)
        def _dbg2(m: str) -> None:
            m = m.rstrip("\n")
            try:
                self.monitor_page.terminal.local_echo(m)
            except Exception:
                pass
            try:
                sys.stdout.write(m + "\n"); sys.stdout.flush()
            except Exception:
                pass
        job.debug.connect(_dbg2)
        job.setParent(self)
        self._bg_jobs.append(job)
        def _finished_cleanup() -> None:
            if job in self._bg_jobs:
                self._bg_jobs.remove(job)
            if from_click:
                try:
                    self.monitor_page.conda_refresh.setEnabled(True)
                    try:
                        self.monitor_page.conda_refresh.setText("Refresh envs")
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass
        job.finished.connect(_finished_cleanup)
        job.start()
    
    def _autosave_runner(self, runner: Dict[str, Any] | None = None) -> None:
        # Save current runner config (including compose fields) if connected
        key = self._host_key()
        if not key:
            return
        r = runner or self._collect_runner()
        try:
            config_store.save_runner(self._config, key, r.get("mode", "default"), r)
            # Print to terminal for visibility
            try:
                sys.stdout.write("[save] runner saved for %s\n" % key); sys.stdout.flush()
            except Exception:
                pass
        except Exception:
            pass
    def _on_conda_envs(self, envs: list) -> None:
        self.monitor_page.conda_combo.clear()
        self.monitor_page.conda_combo.addItems(envs or [])
        # Try auto-select isaaclab-like env
        for target in ["isaaclab", "isaac", "base"]:
            idx = self.monitor_page.conda_combo.findText(target)
            if idx >= 0:
                self.monitor_page.conda_combo.setCurrentIndex(idx)
                break
        self.status.showMessage(f"Conda environments detected: {len(envs)}", 5000)
        # Re-enable refresh button
        try:
            self.monitor_page.conda_refresh.setEnabled(True)
        except Exception:
            pass
        # Print to terminal for visibility
        try:
            sys.stdout.write("[conda-detect] envs: %s\n" % (", ".join(envs) if envs else "<none>")); sys.stdout.flush()
        except Exception:
            pass
        if not envs:
            try:
                self.monitor_page.terminal.local_echo("[conda-detect] No environments detected; type name manually or adjust init path.")
            except Exception:
                pass

    def _detect_remote_docker_containers(self, from_click: bool = False) -> None:
        hp = self._host_params
        # Trace entry as early as possible
        try:
            sys.stdout.write(f"[docker-detect] enter from_click={from_click} hp_present={bool(hp)}\n"); sys.stdout.flush()
        except Exception:
            pass
        # Robust import with absolute fallback and visible error
        try:
            from .ssh_exec import DockerContainerListJob
        except Exception as e1:
            try:
                from gpu_manager_gui.ssh_exec import DockerContainerListJob  # type: ignore
            except Exception as e2:
                try:
                    sys.stdout.write(f"[docker-detect] import failed: {e1 or e2}\n"); sys.stdout.flush()
                except Exception:
                    pass
                return
        if not hp:
            if from_click:
                try:
                    self.monitor_page.terminal.local_echo("[ui] Not connected; cannot list containers")
                except Exception:
                    pass
            return
        if from_click:
            try:
                self.monitor_page.terminal.local_echo("[ui] Refresh containers clicked at %s" % time.strftime('%H:%M:%S'))
            except Exception:
                pass
            try:
                sys.stdout.write("[ui] Refresh containers clicked; host=%s user=%s port=%s\n" % (hp.get('host'), hp.get('username'), hp.get('port')))
                sys.stdout.flush()
            except Exception:
                pass
            try:
                # Use unified refresh button for visual feedback
                self.monitor_page.conda_refresh.setEnabled(False)
                try:
                    self.monitor_page.conda_refresh.setText("Refreshing…")
                except Exception:
                    pass
                QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            except Exception:
                pass
        try:
            self.status.showMessage("Refreshing remote docker containers…")
        except Exception:
            pass
        job = DockerContainerListJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"))
        def _on_res(names: list) -> None:
            try:
                self.monitor_page.docker_combo.clear()
                self.monitor_page.docker_combo.addItems(names or [])
                self.status.showMessage(f"Docker containers detected: {len(names)}", 5000)
                # Print to terminal for visibility
                sys.stdout.write("[docker-detect] containers: %s\n" % (", ".join(names) if names else "<none>"))
                sys.stdout.flush()
            except Exception:
                pass
        def _on_err(m: str) -> None:
            try:
                self.monitor_page.terminal.local_echo(("[docker-detect:error] " + (m or "")).rstrip("\n"))
            except Exception:
                pass
            try:
                sys.stdout.write("[docker-detect:error] %s\n" % (m or ""))
                sys.stdout.flush()
            except Exception:
                pass
        job.result.connect(_on_res)
        job.error.connect(_on_err)
        def _dbg(s: str) -> None:
            s = s.rstrip("\n")
            try:
                self.monitor_page.terminal.local_echo(s)
            except Exception:
                pass
            try:
                sys.stdout.write(s + "\n"); sys.stdout.flush()
            except Exception:
                pass
        job.debug.connect(_dbg)
        job.setParent(self)
        self._bg_jobs.append(job)
        def _done() -> None:
            if job in self._bg_jobs:
                self._bg_jobs.remove(job)
            if from_click:
                try:
                    self.monitor_page.conda_refresh.setEnabled(True)
                    try:
                        self.monitor_page.conda_refresh.setText("Refresh")
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    QApplication.restoreOverrideCursor()
                except Exception:
                    pass
        job.finished.connect(_done)
        job.start()

    

    def _apply_runner_fields(self, r: Dict[str, Any]) -> None:
        # populate fields from runner dict and update preview
        try:
            self.monitor_page.conda_combo.setCurrentText(r.get("conda_env", ""))
            self.monitor_page.use_docker_cb.setChecked(bool(r.get("use_docker", False)))
            self.monitor_page.docker_combo.setCurrentText(r.get("docker_container", ""))
            # Compose fields
            if hasattr(self.monitor_page, 'use_compose_cb'):
                self.monitor_page.use_compose_cb.setChecked(bool(r.get("use_compose", False)))
            if hasattr(self.monitor_page, 'compose_dir_edit'):
                self.monitor_page.compose_dir_edit.setText(r.get("compose_dir", ""))
            if hasattr(self.monitor_page, 'compose_service_edit'):
                self.monitor_page.compose_service_edit.setText(r.get("compose_service", ""))
            self.monitor_page.script_edit.setText(r.get("script", ""))
            # params
            self.monitor_page.params_table.setRowCount(0)
            for k, v in r.get("params", []):
                row = self.monitor_page.params_table.rowCount()
                self.monitor_page.params_table.insertRow(row)
                self.monitor_page.params_table.setItem(row, 0, QTableWidgetItem(str(k)))
                self.monitor_page.params_table.setItem(row, 1, QTableWidgetItem(str(v)))
            # env
            self.monitor_page.env_table.setRowCount(0)
            for k, v in r.get("env", []):
                row = self.monitor_page.env_table.rowCount()
                self.monitor_page.env_table.insertRow(row)
                self.monitor_page.env_table.setItem(row, 0, QTableWidgetItem(str(k)))
                self.monitor_page.env_table.setItem(row, 1, QTableWidgetItem(str(v)))
            self._update_runner_preview()
        except Exception:
            pass

    # Presets --------------------------------------------------------------
    def _refresh_presets(self) -> None:
        names = config_store.list_runner_presets(self._config)
        try:
            cb = self.monitor_page.preset_combo
            cur = cb.currentText().strip()
            cb.blockSignals(True)
            cb.clear()
            for n in names:
                cb.addItem(n)
            if cur:
                cb.setEditText(cur)
            cb.blockSignals(False)
        except Exception:
            pass

    def _save_preset(self) -> None:
        name = self.monitor_page.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Please input a preset name")
            return
        r = self._collect_runner()
        config_store.save_runner_preset(self._config, name, r)
        # Also persist current runner for the connected host so Save acts as an explicit save.
        try:
            self._autosave_runner(r)
        except Exception:
            pass
        self.status.showMessage(f"Saved preset '{name}' (compose={bool(r.get('use_compose'))})")
        self._refresh_presets()

    def _load_preset_into_ui(self) -> None:
        name = self.monitor_page.preset_combo.currentText().strip()
        if not name:
            return
        r = config_store.load_runner_preset(self._config, name)
        if not r:
            QMessageBox.warning(self, "Preset", f"Preset '{name}' not found")
            return
        self._apply_runner_fields(r)
        self.status.showMessage(f"Loaded preset '{name}' into UI", 4000)

    def _delete_preset(self) -> None:
        name = self.monitor_page.preset_combo.currentText().strip()
        if not name:
            return
        resp = QMessageBox.question(self, "Delete preset", f"Delete preset '{name}'?")
        if resp == QMessageBox.StandardButton.Yes:
            config_store.delete_runner_preset(self._config, name)
            self._refresh_presets()
            self.status.showMessage(f"Deleted preset '{name}'", 4000)


def main() -> None:
    # On macOS, suppress noisy system IMK/TSM logs that clutter the terminal.
    def _install_macos_stderr_filter() -> None:
        if sys.platform != "darwin":
            return
        try:
            r_fd, w_fd = os.pipe()
            orig_err = os.dup(2)
            os.dup2(w_fd, 2)
            os.close(w_fd)
            patterns = [
                "IMKCFRunLoopWakeUpReliable",
                "AdjustCapsLockLEDForKeyTransitionHandling",
                "error messaging the mach port",
            ]
            rx = re.compile("|".join(re.escape(p) for p in patterns))

            def _reader() -> None:
                with os.fdopen(r_fd, "r", errors="ignore", buffering=1) as rf, os.fdopen(orig_err, "w", buffering=1) as out:
                    for line in rf:
                        if rx.search(line):
                            # Drop known macOS IMK/TSM noise lines
                            continue
                        out.write(line)
                        out.flush()

            t = threading.Thread(target=_reader, name="stderr-filter", daemon=True)
            t.start()
        except Exception:
            pass

    _install_macos_stderr_filter()

    app = QApplication(sys.argv)
    # Global styles: unify all buttons and inputs across pages
    try:
        app.setStyleSheet(
            """
            QGroupBox { font-weight: 600; border: 1px solid #dcdce0; border-radius: 8px; margin-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; background: transparent; }
            QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { min-height: 28px; padding: 4px 6px; border: 1px solid #c9c9ce; border-radius: 6px; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #2d7ef7; }

            /* Buttons (global) */
            QPushButton, QToolButton { min-height: 32px; padding: 0 12px; border-radius: 6px; border: 1px solid #2d7ef7; color: #2d7ef7; background: #ffffff; }
            QPushButton:hover, QToolButton:hover { background: #f0f6ff; }
            QPushButton:pressed, QToolButton:pressed { background: #dbe9ff; border: 1px solid #1e6de6; color: #1e6de6; }
            QPushButton:disabled, QToolButton:disabled { color: #9bb5ec; border: 1px solid #b7cbf5; background: #f5f8ff; }
            QPushButton:checked, QToolButton:checked { background: #eaf2ff; border: 1px solid #2d7ef7; color: #1e6de6; }

            QPushButton#primaryButton, QToolButton#primaryButton { background: #2d7ef7; color: white; border: 1px solid #2d7ef7; }
            QPushButton#primaryButton:hover, QToolButton#primaryButton:hover { background: #3a86f8; }
            QPushButton#primaryButton:pressed, QToolButton#primaryButton:pressed { background: #1e6de6; border: 1px solid #1e6de6; }
            QPushButton#primaryButton:disabled, QToolButton#primaryButton:disabled { background: #9dbcf7; color: white; border: 1px solid #9dbcf7; }

            QLabel { color: #222; }
            """
        )
    except Exception:
        pass
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
