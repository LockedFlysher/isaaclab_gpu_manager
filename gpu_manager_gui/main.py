from __future__ import annotations

import os
import sys
import time
from typing import Optional, Dict, Any
import shlex
import subprocess

from PyQt6.QtCore import Qt, QThread, pyqtSignal
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
)
from PyQt6.QtCharts import QChart, QChartView, QPieSeries
from PyQt6.QtWidgets import QTabWidget, QPlainTextEdit, QTableWidget, QTableWidgetItem, QPushButton

# Support both `python -m gpu_manager_gui.main` and direct script run
try:
    from .ssh_worker import SSHGpuPoller, Snapshot
    from .ssh_exec import SSHCommandJob
    from . import config_store
except Exception:  # running as a script: fix sys.path and import absolutely
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from gpu_manager_gui.ssh_worker import SSHGpuPoller, Snapshot
    from gpu_manager_gui.ssh_exec import SSHCommandJob
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
            QPushButton { min-height: 36px; padding: 0 14px; border-radius: 6px; border: 1px solid #2d7ef7; color: #2d7ef7; background: #ffffff; }
            QPushButton#primaryButton { background: #2d7ef7; color: white; border: 1px solid #2d7ef7; }
            QPushButton#primaryButton:disabled { background: #9dbcf7; }
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


class MonitorPage(QWidget):
    disconnect_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.disconnect_btn = QPushButton("Disconnect")
        top.addStretch(1)
        top.addWidget(self.disconnect_btn)
        layout.addLayout(top)

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
        self.chart = QChart()
        self.chart.setTitle("Per-user VRAM (MiB)")
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        hbox.addWidget(self.gpu_table, 3)
        hbox.addWidget(self.chart_view, 2)
        self.disconnect_btn.clicked.connect(lambda: self.disconnect_requested.emit())

        # IsaacLab Runner panel -------------------------------------------
        runner_box = QGroupBox("IsaacLab Command Runner")
        r_v = QVBoxLayout(runner_box)
        self.mode_tabs = QTabWidget()
        self.mode_tabs.addTab(QWidget(), "train")
        self.mode_tabs.addTab(QWidget(), "play")
        r_v.addWidget(self.mode_tabs)

        # Top row: script (left) and conda env (right)
        self.script_edit = QLineEdit(); self.script_edit.setPlaceholderText("/path/to/train.py or play.py")
        self.conda_combo = QComboBox(); self.conda_combo.setEditable(True); self.conda_refresh = QPushButton("Refresh envs"); self.debug_cb = QCheckBox("Debug")
        top_row = QHBoxLayout()
        # Left: script
        top_row.addWidget(QLabel("script"))
        top_row.addWidget(self.script_edit, 2)
        top_row.addSpacing(12)
        # Right: conda env
        top_row.addWidget(QLabel("conda env"))
        top_row.addWidget(self.conda_combo, 1)
        top_row.addWidget(self.conda_refresh)
        top_row.addWidget(self.debug_cb)
        r_v.addLayout(top_row)

        form_row = QHBoxLayout()
        left_form = QFormLayout(); right_form = QFormLayout()
        # (script moved to top row)
        # Params table (--key=value)
        self.params_table = QTableWidget(0, 2); self.params_table.setHorizontalHeaderLabels(["param", "value"])
        self.params_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.params_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.params_add = QPushButton("+ param"); self.params_del = QPushButton("- param")
        left_form.addRow(self.params_table)
        left_btns = QHBoxLayout(); left_btns.addWidget(self.params_add); left_btns.addWidget(self.params_del); left_btns.addStretch(1)
        left_form.addRow(left_btns)
        # Env table (KEY=VALUE)
        self.env_table = QTableWidget(0, 2); self.env_table.setHorizontalHeaderLabels(["env", "value"])
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.env_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.env_add = QPushButton("+ env"); self.env_del = QPushButton("- env")
        right_form.addRow(self.env_table)
        right_btns = QHBoxLayout(); right_btns.addWidget(self.env_add); right_btns.addWidget(self.env_del); right_btns.addStretch(1)
        right_form.addRow(right_btns)
        form_row.addLayout(left_form, 1); form_row.addLayout(right_form, 1)
        r_v.addLayout(form_row)

        # Preview + Run
        self.preview_edit = QLineEdit(); self.preview_edit.setReadOnly(True)
        self.run_btn = QPushButton("Run")
        pr = QHBoxLayout(); pr.addWidget(QLabel("preview")); pr.addWidget(self.preview_edit, 1); pr.addWidget(self.run_btn)
        r_v.addLayout(pr)

        # Output log
        self.output_log = QPlainTextEdit(); self.output_log.setReadOnly(True)
        r_v.addWidget(self.output_log, 1)

        # Tabs: Monitor vs Runner
        self.main_tabs = QTabWidget()
        monitor_tab = QWidget(); mt_l = QVBoxLayout(monitor_tab); mt_l.addWidget(center, 1)
        runner_tab = QWidget(); rt_l = QVBoxLayout(runner_tab); rt_l.addWidget(runner_box, 1)
        self.main_tabs.addTab(monitor_tab, "Monitor")
        self.main_tabs.addTab(runner_tab, "Runner")
        layout.addWidget(self.main_tabs, 1)

        # Wire runner buttons
        self.params_add.clicked.connect(lambda: self._add_row(self.params_table))
        self.params_del.clicked.connect(lambda: self._del_selected(self.params_table))
        self.env_add.clicked.connect(lambda: self._add_row(self.env_table))
        self.env_del.clicked.connect(lambda: self._del_selected(self.env_table))
        # Run click is wired in MainWindow to ensure lifecycle
        self.conda_refresh.clicked.connect(self._on_refresh_conda)

    def _on_refresh_conda(self) -> None:
        # Delegate to MainWindow to trigger detection
        p = self.parent()
        if p and hasattr(p, "_detect_remote_conda_envs"):
            p._detect_remote_conda_envs()

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
        return "train" if self.mode_tabs.currentIndex() == 0 else "play"



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

        # Pages
        self.stack = QStackedWidget()
        self.login_page = LoginPage()
        self.monitor_page = MonitorPage()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.monitor_page)
        self.setCentralWidget(self.stack)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # Wiring
        self.login_page.connect_requested.connect(self._begin_connect)
        self.login_page.test_requested.connect(self._test_connect)
        self.monitor_page.disconnect_requested.connect(self._disconnect)
        self.login_page.profile_combo.currentTextChanged.connect(self._load_profile_into_fields)
        # Runner actions
        self.monitor_page.run_btn.clicked.connect(self._run_runner)
        self.monitor_page.conda_combo.currentTextChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.script_edit.textChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.params_table.itemChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.env_table.itemChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.mode_tabs.currentChanged.connect(lambda _=None: self._load_runner_config())
        self.monitor_page.conda_refresh.clicked.connect(self._detect_remote_conda_envs)
        self.monitor_page.conda_refresh.clicked.connect(self._detect_remote_conda_envs)

        # Load profiles
        self._refresh_profiles()
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
        self._detect_remote_conda_envs()

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
        self.monitor_page.update_snapshot(snap)
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
            if not self.monitor_page.debug_cb.isChecked():
                return
        except Exception:
            pass
        self.monitor_page.output_log.appendPlainText(text.rstrip("\n"))

    def _on_poller_finished(self) -> None:
        self._poller = None
        if self.stack.currentIndex() == 1:
            self.stack.setCurrentIndex(0)
            self.status.showMessage("Disconnected")

    # Runner command build/run -------------------------------------------
    def _collect_runner(self) -> Dict[str, Any]:
        mode = self.monitor_page.get_mode()
        conda_env = self.monitor_page.conda_combo.currentText().strip()
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
        return {"mode": mode, "conda_env": conda_env, "script": script, "params": params, "env": env}

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
        import shlex as _sh
        r = self._collect_runner()
        env_dict = {k: v for k, v in r.get("env", [])}
        base = self._build_python_cmd(r)
        inner = SSHCommandJob.build_inner(env=env_dict, conda_env=(r.get("conda_env") or None), base_cmd=base)
        self.monitor_page.preview_edit.setText(inner)

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
        inner = SSHCommandJob.build_inner(env=env_dict, conda_env=(r.get("conda_env") or None), base_cmd=base)
        job = SSHCommandJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"), inner)
        self.monitor_page.output_log.clear()
        self.monitor_page.run_btn.setEnabled(False)
        job.line.connect(lambda s: self.monitor_page.output_log.appendPlainText(s.rstrip("\n")))
        job.error.connect(lambda m: self.monitor_page.output_log.appendPlainText(f"[error] {m}"))
        def _done(rc: int) -> None:
            self.monitor_page.output_log.appendPlainText(f"\n[exit] rc={rc}")
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
        mode = self.monitor_page.get_mode()
        r = config_store.load_runner(self._config, key, mode)
        # populate fields
        self.monitor_page.conda_combo.setCurrentText(r.get("conda_env", ""))
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

    def _detect_remote_conda_envs(self) -> None:
        try:
            from .ssh_exec import CondaEnvListJob
        except Exception:
            return
        hp = self._host_params
        if not hp:
            return
        # Disable refresh button while running and add status
        try:
            self.monitor_page.conda_refresh.setEnabled(False)
        except Exception:
            pass
        self.status.showMessage("Refreshing remote conda environments…")
        job = CondaEnvListJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"))
        job.result.connect(self._on_conda_envs)
        # Mirror errors to output when Debug is checked
        job.error.connect(lambda m: (self.status.showMessage(m, 5000), self.monitor_page.output_log.appendPlainText(m) if getattr(self.monitor_page, 'debug_cb', None) and self.monitor_page.debug_cb.isChecked() else None))
        # CondaEnvListJob emits debug logs on stderr; mirror to Output when Debug is checked
        try:
            job.debug.connect(lambda m: (self.monitor_page.debug_cb.isChecked() and self.monitor_page.output_log.appendPlainText(m.rstrip("\n"))))
        except Exception:
            pass
        job.setParent(self)
        self._bg_jobs.append(job)
        job.finished.connect(lambda: self._bg_jobs.remove(job) if job in self._bg_jobs else None)
        job.start()

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
        if not envs and hasattr(self.monitor_page, 'output_log'):
            self.monitor_page.output_log.appendPlainText("[conda-detect] No environments detected; type name manually or adjust init path.")


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
