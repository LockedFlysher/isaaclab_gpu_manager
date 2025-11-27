from __future__ import annotations

import os
import sys
import time
from typing import Optional
import subprocess

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QGroupBox,
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
)
from PyQt6.QtCharts import QChart, QChartView, QPieSeries

# Support both `python -m gpu_manager_gui.main` and direct script run
try:
    from .ssh_worker import SSHGpuPoller, Snapshot
except Exception:  # running as a script: fix sys.path and import absolutely
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from gpu_manager_gui.ssh_worker import SSHGpuPoller, Snapshot


class LoginPage(QWidget):
    connect_requested = pyqtSignal(str, int, Optional[str], float)

    def __init__(self) -> None:
        super().__init__()
        box = QGroupBox("SSH Login")
        g = QGridLayout(box)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("user@server or hostname")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.ident_edit = QLineEdit()
        self.ident_edit.setPlaceholderText("Optional identity file (~/.ssh/id_rsa)")
        self.browse_btn = QPushButton("Browse…")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 600)
        self.interval_spin.setValue(5)
        self.connect_btn = QPushButton("Connect")

        g.addWidget(QLabel("Host"), 0, 0)
        g.addWidget(self.host_edit, 0, 1, 1, 3)
        g.addWidget(QLabel("Port"), 1, 0)
        g.addWidget(self.port_spin, 1, 1)
        g.addWidget(QLabel("Identity"), 1, 2)
        g.addWidget(self.ident_edit, 1, 3)
        g.addWidget(self.browse_btn, 1, 4)
        g.addWidget(QLabel("Interval(s)"), 2, 0)
        g.addWidget(self.interval_spin, 2, 1)
        g.addWidget(self.connect_btn, 2, 4)

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(box)
        outer.addStretch(2)

        self.browse_btn.clicked.connect(self._browse_identity)
        self.connect_btn.clicked.connect(self._emit_connect)

    def _browse_identity(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SSH identity file", os.path.expanduser("~/.ssh"))
        if path:
            self.ident_edit.setText(path)

    def _emit_connect(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing host", "Please enter host as user@server or hostname")
            return
        port = int(self.port_spin.value())
        ident = self.ident_edit.text().strip() or None
        interval = float(self.interval_spin.value())
        self.connect_requested.emit(host, port, ident, interval)


class MonitorPage(QWidget):
    disconnect_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        # Top bar with a disconnect button
        top = QHBoxLayout()
        self.disconnect_btn = QPushButton("Disconnect")
        top.addStretch(1)
        top.addWidget(self.disconnect_btn)
        layout.addLayout(top)

        # Center split: table + pie chart
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
        # Table
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

        # Pie chart
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

    def __init__(self, host: str, port: int, identity: Optional[str], ssh_bin: str = "ssh", timeout: float = 8.0) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._identity = identity
        self._ssh_bin = ssh_bin
        self._timeout = float(timeout)

    def run(self) -> None:  # type: ignore[override]
        cmd = [self._ssh_bin, "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        cmd += [self._host, "--", "bash", "-lc", "nvidia-smi -L || nvidia-smi --query-gpu=index --format=csv,noheader,nounits"]
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

        # Pages
        self.stack = QStackedWidget()
        self.login_page = LoginPage()
        self.monitor_page = MonitorPage()
        self.stack.addWidget(self.login_page)   # index 0
        self.stack.addWidget(self.monitor_page) # index 1
        self.setCentralWidget(self.stack)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # Wiring
        self.login_page.connect_requested.connect(self._begin_connect)
        self.monitor_page.disconnect_requested.connect(self._disconnect)

    def closeEvent(self, event):  # type: ignore[override]
        if self._poller is not None:
            self._poller.stop()
            self._poller.requestInterruption()
            self._poller.wait(500)
            self._poller = None
        return super().closeEvent(event)

    def _begin_connect(self, host: str, port: int, identity: Optional[str], interval: float) -> None:
        self.status.showMessage("Testing SSH connection…")
        self.login_page.connect_btn.setEnabled(False)
        self._pending_params = (host, port, identity, interval)
        self._tester = ConnectTester(host, port, identity)
        self._tester.finished_ok.connect(self._on_connect_ok)
        self._tester.failed.connect(self._on_connect_failed)
        self._tester.finished.connect(lambda: self.login_page.connect_btn.setEnabled(True))
        self._tester.start()

    def _on_connect_ok(self) -> None:
        host, port, ident, interval = self._pending_params  # type: ignore[attr-defined]
        self._poller = SSHGpuPoller(host=host, port=port, identity_file=ident, interval_sec=interval)
        self._poller.snapshot_ready.connect(self._on_snapshot)
        self._poller.error_msg.connect(self._on_error)
        self._poller.finished.connect(self._on_poller_finished)
        self._poller.start()
        self._cur_host = host
        self.stack.setCurrentIndex(1)
        self.setWindowTitle(f"IsaacLab GPU Manager — {host}")
        self.status.showMessage("Connected. Polling every %.0f s" % interval)

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
