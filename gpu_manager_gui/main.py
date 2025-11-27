from __future__ import annotations

import os
import sys
import time
from typing import Optional, Dict, Any
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
)
from PyQt6.QtCharts import QChart, QChartView, QPieSeries

# Support both `python -m gpu_manager_gui.main` and direct script run
try:
    from .ssh_worker import SSHGpuPoller, Snapshot
    from . import config_store
except Exception:  # running as a script: fix sys.path and import absolutely
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from gpu_manager_gui.ssh_worker import SSHGpuPoller, Snapshot
    from gpu_manager_gui import config_store


class LoginPage(QWidget):
    # host, port, username, identity, password, interval
    connect_requested = pyqtSignal(str, int, object, object, object, float)

    def __init__(self) -> None:
        super().__init__()
        box = QGroupBox("SSH Login")
        vbox = QVBoxLayout(box)

        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(False)
        self.profile_label = QLabel("Profile")
        profile_row = QHBoxLayout()
        profile_row.addWidget(self.profile_label)
        profile_row.addWidget(self.profile_combo, 1)
        vbox.addLayout(profile_row)

        # Required fields
        req = QFormLayout()
        req.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("server or user@server")
        self.host_edit.setToolTip("Required: hostname or user@hostname")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.port_spin.setToolTip("Required: SSH port (default 22)")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 600)
        self.interval_spin.setValue(5)
        self.interval_spin.setToolTip("Required: polling interval in seconds")
        req.addRow(QLabel("Host *"), self.host_edit)
        req.addRow(QLabel("Port *"), self.port_spin)
        req.addRow(QLabel("Interval(s) *"), self.interval_spin)
        vbox.addLayout(req)

        # Optional fields
        opt = QFormLayout()
        opt.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("username (optional)")
        self.user_edit.setToolTip("Optional: leave empty to use system ssh defaults or user@ in Host")
        self.ident_edit = QLineEdit()
        self.ident_edit.setPlaceholderText("~/.ssh/id_rsa (optional)")
        self.ident_edit.setToolTip("Optional private key path; leave empty to use ssh-agent/keys")
        self.browse_btn = QPushButton("Browse…")
        ident_row = QHBoxLayout()
        ident_row.addWidget(self.ident_edit, 1)
        ident_row.addWidget(self.browse_btn)
        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("password (optional)")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_cb = QCheckBox("Remember password (insecure)")
        self.remember_cb.setToolTip("Stores password base64 in YAML; not secure; for convenience only")
        pass_row = QHBoxLayout()
        pass_row.addWidget(self.pass_edit, 1)
        self.show_pass_cb = QCheckBox("Show")
        pass_row.addWidget(self.show_pass_cb)
        opt.addRow(QLabel("User"), self.user_edit)
        opt.addRow(QLabel("Identity"), ident_row)
        opt.addRow(QLabel("Password"), pass_row)
        opt.addRow(QLabel(""), self.remember_cb)
        vbox.addLayout(opt)

        # Footer
        self.req_note = QLabel("Fields marked * are required")
        self.req_note.setStyleSheet("color: gray;")
        vbox.addWidget(self.req_note)
        self.connect_btn = QPushButton("Connect")
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.connect_btn)
        vbox.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(box)
        outer.addStretch(2)

        self.browse_btn.clicked.connect(self._browse_identity)
        self.connect_btn.clicked.connect(self._emit_connect)
        self.profile_combo.currentTextChanged.connect(self._profile_changed)
        self.show_pass_cb.toggled.connect(self._toggle_password_echo)

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
        layout.addWidget(center, 1)
        self.disconnect_btn.clicked.connect(lambda: self.disconnect_requested.emit())

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

        series = QPieSeries()
        series.setLabelsVisible(True)
        totals = snap.user_vram_mib
        if not totals:
            series.append("idle", 1)
        else:
            for user, mib in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
                series.append(f"{user} ({mib} MiB)", max(0.01, float(mib)))
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("Per-user VRAM (MiB)")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self.chart_view.setChart(chart)


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
        self.monitor_page.disconnect_requested.connect(self._disconnect)
        self.login_page.profile_combo.currentTextChanged.connect(self._load_profile_into_fields)

        # Load profiles
        self._refresh_profiles()
        last_key = self._config.get("last_used_key")
        if last_key and last_key in self._config.get("profiles", {}):
            self.login_page.profile_combo.setCurrentText(last_key)
            self._load_profile_into_fields(last_key)

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
        self.stack.setCurrentIndex(1)
        self.setWindowTitle(f"IsaacLab GPU Manager — {host}")
        self.status.showMessage("Connected. Polling every %.0f s" % interval)
        # Save profile on success
        remember = self.login_page.remember_cb.isChecked()
        prof = dict(host=host, port=port, username=user or "", identity=ident or "", interval=interval, remember_password=remember)
        config_store.save_profile(self._config, prof, password if remember else None)
        self._refresh_profiles()
        self.login_page.profile_combo.setCurrentText(config_store.make_key(host, port, user))

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

    # Slots from worker
    def _on_snapshot(self, snap: Snapshot) -> None:
        self.monitor_page.update_snapshot(snap)
        self.status.showMessage(
            f"Last update: {time.strftime('%H:%M:%S')} | GPUs: {len(snap.gpus)} | users: {len(snap.user_vram_mib)}"
        )

    def _on_error(self, msg: str) -> None:
        if msg:
            self.status.showMessage(msg, 5000)

    def _on_poller_finished(self) -> None:
        self._poller = None
        if self.stack.currentIndex() == 1:
            self.stack.setCurrentIndex(0)
            self.status.showMessage("Disconnected")


def main() -> None:
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
