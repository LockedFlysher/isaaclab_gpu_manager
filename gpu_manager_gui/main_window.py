from __future__ import annotations

import os
import re
import shlex
import sys
import time
from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStatusBar, QStackedWidget, QTableWidgetItem

from .ssh_worker import SSHGpuPoller, Snapshot
from .ssh_exec import SSHCommandJob, RemoteOSInfoJob, CondaEnvListJob, SSHInteractiveShell, ReverseTunnelParamikoJob
from .terminal_widget import TerminalWidget
from . import config_store
from .login_page import LoginPage
from .monitor_page import MonitorPage
from .remote_file_dialog import RemoteFileDialog


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
        # key/agent mode via subprocess ssh
        cmd = [self._ssh_bin, "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        dest = f"{self._username}@{self._host}" if self._username else self._host
        cmd += [dest, "--", "bash", "-lc", "nvidia-smi -L || nvidia-smi --query-gpu=index --format=csv,noheader,nounits"]
        try:
            import subprocess
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
        self._console_shells: Dict[TerminalWidget, SSHInteractiveShell] = {}
        self._console_auto_opened: bool = False
        self._reverse_tunnel: ReverseTunnelParamikoJob | None = None
        # Ensure graceful shutdown on app exit
        try:
            QApplication.instance().aboutToQuit.connect(self._graceful_shutdown)  # type: ignore[arg-type]
        except Exception:
            pass

        # Pages
        self.stack = QStackedWidget()
        self.login_page = LoginPage()
        self.monitor_page = MonitorPage()
        try:
            self.monitor_page._mw = self
        except Exception:
            pass
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.monitor_page.main_tabs)
        self.setCentralWidget(self.stack)

        # Status bar
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # Wiring
        self.login_page.connect_requested.connect(self._begin_connect)
        self.login_page.test_requested.connect(self._test_connect)
        self.monitor_page.disconnect_requested.connect(self._disconnect)
        try:
            self.monitor_page._mw = self
        except Exception:
            pass
        try:
            self.monitor_page.docker_refresh_req.connect(lambda: self._detect_remote_docker_containers(True))
            self.monitor_page.conda_refresh_req.connect(lambda: self._detect_remote_conda_envs(True))
            self.monitor_page.preview_update_req.connect(self._update_runner_preview)
            # Also refresh Console preview on any preview update request
            self.monitor_page.preview_update_req.connect(self._update_console_preview)
        except Exception:
            pass
        self.login_page.profile_combo.currentTextChanged.connect(self._load_profile_into_fields)
        self.monitor_page.conda_combo.currentTextChanged.connect(lambda _=None: self._update_runner_preview())
        try:
            self.monitor_page.docker_combo.currentTextChanged.connect(lambda _=None: (self._update_runner_preview(), self._autosave_runner()))
            self.monitor_page.use_docker_cb.toggled.connect(lambda _=None: (self._update_runner_preview(), self._autosave_runner()))
        except Exception:
            pass
        try:
            self.monitor_page.console_profile_combo.currentTextChanged.connect(lambda _=None: self._update_console_preview())
        except Exception:
            pass
        self.monitor_page.script_edit.textChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.params_table.itemChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.env_table.itemChanged.connect(lambda _=None: self._update_runner_preview())
        self.monitor_page.conda_refresh.clicked.connect(lambda: self._refresh_runner_envs(True))
        try:
            self.monitor_page.preset_save.clicked.connect(self._save_preset)
            self.monitor_page.preset_load.clicked.connect(self._load_preset_into_ui)
            self.monitor_page.preset_del.clicked.connect(self._delete_preset)
        except Exception:
            pass

        try:
            self.monitor_page.main_tabs._bar.currentChanged.connect(self._on_main_tab_changed)
        except Exception:
            pass

        # Load profiles list and presets list
        try:
            self._refresh_profiles()
        except Exception:
            pass
        try:
            self._refresh_presets()
        except Exception:
            pass
        try:
            self.monitor_page.update_console_preset_visibility()
        except Exception:
            pass

        # Auto-connect on startup if configured
        try:
            cfg = self._config
            if cfg.get("auto_connect_last_used") and cfg.get("last_used_key"):
                prof = cfg.get("profiles", {}).get(cfg.get("last_used_key"))
                if prof:
                    self._fill_login_fields_from_profile(prof)
                    self._begin_connect(prof.get("host"), int(prof.get("port", 22)), prof.get("username") or None, prof.get("identity") or None, config_store.get_profile_password(prof), float(prof.get("interval", 5.0)))
        except Exception:
            pass

    # Login/profile helpers ----------------------------------------------
    def _refresh_profiles(self) -> None:
        cfg = self._config
        profs = cfg.get("profiles", {}) or {}
        keys = list(profs.keys())
        try:
            self.login_page.set_profiles(keys)
        except Exception:
            pass
        if cfg.get("last_used_key") and cfg.get("last_used_key") in keys:
            try:
                self.login_page.profile_combo.setCurrentText(cfg.get("last_used_key"))
            except Exception:
                pass

    def _fill_login_fields_from_profile(self, prof: Dict[str, Any]) -> None:
        # Build a dict including decoded password (if present/remembered)
        dd = dict(prof)
        try:
            pw = config_store.get_profile_password(prof)
            if pw:
                dd["password"] = pw
        except Exception:
            pass
        self.login_page.fill_from_profile(dd)

    def _load_profile_into_fields(self, key: str) -> None:
        prof = self._config.get("profiles", {}).get(key)
        if prof:
            self._fill_login_fields_from_profile(prof)

    # Connect / disconnect -----------------------------------------------
    def _test_connect(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], interval: float) -> None:
        t = ConnectTester(host, int(port), username, identity, password)
        t.finished_ok.connect(lambda: QMessageBox.information(self, "Test", "Connection OK"))
        t.failed.connect(lambda m: QMessageBox.warning(self, "Test", m or "connect failed"))
        t.setParent(self)
        self._test_threads.append(t)
        def _done() -> None:
            if t in self._test_threads:
                self._test_threads.remove(t)
        t.finished.connect(_done)
        t.start()

    def _begin_connect(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], interval: float) -> None:
        # Save profile now (respect remember_password flag on login page)
        try:
            prof = {
                "host": host,
                "port": int(port),
                "username": username or "",
                "identity": identity or "",
                "interval": float(interval),
                "remember_password": bool(self.login_page.remember_cb.isChecked()),
            }
            config_store.save_profile(self._config, prof, password if self.login_page.remember_cb.isChecked() else None)
        except Exception:
            pass
        hp = {"host": host, "port": int(port), "username": username or None, "identity": identity or None, "password": password or None, "interval": float(interval)}
        self._host_params = hp
        self._cur_host = host
        try:
            self.status.showMessage(f"Connecting to {host}:{port}…")
        except Exception:
            pass
        # Start poller (always SSH path; for local use host=127.0.0.1)
        try:
            p = SSHGpuPoller(host, int(port), username or None, password or None, identity or None, float(interval))
            p.snapshot_ready.connect(self._on_snapshot)
            p.error_msg.connect(self._on_error)
            p.finished.connect(self._on_poller_finished)
            p.setParent(self)
            self._poller = p
            p.start()
        except Exception as e:
            QMessageBox.critical(self, "Connect", str(e) or "failed to start poller")
            return
        # Switch to monitor
        self.stack.setCurrentIndex(1)
        # Load OS info asynchronously (remote path covers 127.0.0.1 as well)
        self._fetch_remote_os()
        # Load runner config for this host
        self._load_runner_config()
        # Load presets list into Console preset dropdown visibility
        try:
            self.monitor_page.update_console_preset_visibility()
        except Exception:
            pass
        # Prime conda/envs or docker containers
        self._refresh_runner_envs(False)
        # Autofill Reverse Tunnel target based on current login and lock fields
        try:
            uh = f"{username or ''}@{host}" if (username or '').strip() else host
            self.monitor_page.rvt_user_host.setText(uh)
            self.monitor_page.rvt_user_host.setEnabled(False)
            self.monitor_page.rvt_ssh_port.setValue(int(port))
            self.monitor_page.rvt_ssh_port.setEnabled(False)
        except Exception:
            pass
        # Autofill Reverse Tunnel target based on current login and lock fields
        try:
            uh = f"{username or ''}@{host}" if (username or '').strip() else host
            self.monitor_page.rvt_user_host.setText(uh)
            self.monitor_page.rvt_user_host.setEnabled(False)
            self.monitor_page.rvt_ssh_port.setValue(int(port))
            self.monitor_page.rvt_ssh_port.setEnabled(False)
        except Exception:
            pass

    # Reverse tunnel controls --------------------------------------------
    def _start_reverse_tunnel(self) -> None:
        if self._reverse_tunnel is not None:
            self.status.showMessage("Reverse tunnel already running", 4000)
            return
        ui = self.monitor_page
        # Use current login page credentials for tunnel target
        hp = self._host_params or {}
        host = (hp.get("host") or "").strip()
        if not host:
            QMessageBox.warning(self, "Reverse Tunnel", "Please connect first")
            return
        user = (hp.get("username") or "").strip() or None
        ssh_port = int(hp.get("port") or 22)
        bind_port = int(ui.rvt_bind_port.value())
        local_port = int(ui.rvt_local_port.value())
        identity = None
        try:
            identity = (self._host_params.get("identity") or "").strip() or None
        except Exception:
            identity = None
        job = ReverseTunnelParamikoJob(host, ssh_port, user, hp.get("password") or None, identity, bind_port, local_port, bind_addr="127.0.0.1")
        def _on_started():
            try:
                ui.rvt_start_btn.setEnabled(False); ui.rvt_stop_btn.setEnabled(True)
            except Exception:
                pass
            uh = f"{user}@{host}" if user else host
            self.status.showMessage(f"Reverse tunnel started: -R {bind_port}:localhost:{local_port} -> {uh}:{ssh_port}")
        def _on_stopped(rc: int):
            try:
                ui.rvt_start_btn.setEnabled(True); ui.rvt_stop_btn.setEnabled(False)
            except Exception:
                pass
            self.status.showMessage(f"Reverse tunnel stopped (rc={rc})", 5000)
            self._reverse_tunnel = None
        def _on_error(m: str):
            self.status.showMessage(m or "reverse tunnel failed", 6000)
        def _on_dbg(s: str):
            try:
                sys.stdout.write(s.rstrip("\n")+"\n"); sys.stdout.flush()
            except Exception:
                pass
        job.started.connect(_on_started)
        job.stopped.connect(_on_stopped)
        job.error.connect(_on_error)
        try:
            job.debug.connect(_on_dbg)
        except Exception:
            pass
        job.setParent(self)
        self._reverse_tunnel = job
        job.start()

    def _stop_reverse_tunnel(self) -> None:
        j = self._reverse_tunnel
        if j is None:
            self.status.showMessage("Reverse tunnel not running", 4000)
            return
        try:
            j.stop_tunnel()
        except Exception:
            pass
        try:
            j.wait(2000)
        except Exception:
            pass

    def _disconnect(self) -> None:
        if self._poller is not None:
            try:
                self._poller.stop()
            except Exception:
                pass
        try:
            if self._poller is not None:
                self._poller.wait(1500)
        except Exception:
            pass
        self._poller = None
        self._host_params = {}
        try:
            self.stack.setCurrentIndex(0)
            self.status.showMessage("Disconnected")
        except Exception:
            pass

    # Snapshot/error handlers --------------------------------------------
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
        self._update_runner_preview()

    def _on_error(self, msg: str) -> None:
        if msg:
            self.status.showMessage(msg, 5000)

    def _log_debug(self, text: str) -> None:
        try:
            sys.stdout.write((text.rstrip("\n") + "\n"))
            sys.stdout.flush()
        except Exception:
            pass

    def _on_poller_finished(self) -> None:
        self._poller = None
        if self.stack.currentIndex() == 1:
            self.stack.setCurrentIndex(0)
            self.status.showMessage("Disconnected")

    # Console shell helpers -----------------------------------------------
    def _open_console_shell(self) -> None:
        term = getattr(self.monitor_page, 'console_area', None)
        if term is None:
            return
        t = self.monitor_page.console_area.active_terminal()
        if t in self._console_shells:
            self.status.showMessage("Console already open (this pane)", 3000)
            return
        hp = self._host_params
        if not hp:
            QMessageBox.warning(self, "Not connected", "Please connect first")
            return
        try:
            shell = SSHInteractiveShell(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"), strip_ansi=False)
        except Exception as e:
            QMessageBox.critical(self, "Console", str(e) or "failed to create shell"); return
        self._console_shells[t] = shell
        try:
            t.attach_shell(shell)
        except Exception:
            pass
        shell.data.connect(t.feed)
        shell.error.connect(lambda m, _t=t: _t.local_echo(f"[console:error] {m}"))
        shell.connected.connect(lambda: self.status.showMessage("Console connected", 3000))
        try:
            shell.connected.connect(lambda _t=t: _t.send_resize())
        except Exception:
            pass
        def _closed(_t=t):
            self.status.showMessage("Console closed", 3000)
            try:
                if _t in self._console_shells:
                    self._console_shells.pop(_t, None)
                _t.detach_shell()
            except Exception:
                pass
        shell.closed.connect(_closed)
        shell.start()
        try:
            self.monitor_page.main_tabs._bar.setCurrentIndex(2)
        except Exception:
            pass
        try:
            self.monitor_page.console_area.focus_active()
        except Exception:
            pass

    def _send_console_line(self, text: str) -> None:
        if not text:
            return
        t = self.monitor_page.console_area.active_terminal()
        sh = self._console_shells.get(t)
        if sh is None:
            self.status.showMessage("Console not open (this pane)", 3000)
            return
        try:
            sh.send_line(text)
        except Exception as e:
            try:
                self.status.showMessage(f"Console send failed: {e}", 5000)
            except Exception:
                pass

    def _on_main_tab_changed(self, idx: int) -> None:
        try:
            if idx != 2:
                return
            if self._console_auto_opened:
                return
            if not self._console_shells:
                self._open_console_shell()
            self._console_auto_opened = True
        except Exception:
            pass

    def _run_preview_command(self, cmd: str) -> None:
        if not cmd:
            return
        t = self.monitor_page.console_area.active_terminal()
        if t not in self._console_shells:
            self._open_console_shell()
            try:
                QTimer.singleShot(250, lambda: self._send_console_line(cmd))
            except Exception:
                pass
        else:
            self._send_console_line(cmd)

    def _run_preview_all(self) -> None:
        try:
            r = self._console_runner()
            cmds = self._build_preview_commands(r)
        except Exception:
            cmds = []
        if not cmds:
            self.status.showMessage("No commands to run", 3000)
            return
        t = self.monitor_page.console_area.active_terminal()
        need_open = t not in self._console_shells
        if need_open:
            self._open_console_shell()
        delay = 250 if need_open else 0
        for c in cmds:
            try:
                QTimer.singleShot(delay, lambda cc=c: self._send_console_line(cc))
            except Exception:
                pass
            delay += 800 if 'docker exec -it' in c else 200

    def _open_docker_shell(self) -> None:
        t = self.monitor_page.console_area.active_terminal()
        if t not in self._console_shells:
            self._open_console_shell()
            QThread.msleep(200)
        r = self._console_runner()
        cmd = self._container_enter_cmd(r)
        if not cmd:
            self.status.showMessage("Please select or type a container name in Runner", 5000)
            return
        self._send_console_line(cmd)

    def _close_console_shell(self) -> None:
        t = self.monitor_page.console_area.active_terminal()
        sh = self._console_shells.get(t)
        if sh is None:
            self.status.showMessage("Console already closed (this pane)", 3000)
            return
        try:
            sh.stop_shell()
        except Exception:
            pass
        try:
            t.detach_shell()
        except Exception:
            pass
        self._console_shells.pop(t, None)

    def _close_console_pane(self) -> None:
        t = self.monitor_page.console_area.active_terminal()
        sh = self._console_shells.pop(t, None)
        if sh is not None:
            try:
                sh.stop_shell()
            except Exception:
                pass
            try:
                t.detach_shell()
            except Exception:
                pass
        try:
            self.monitor_page.console_area.close_active()
        except Exception:
            pass

    # Fallback snapshot update (defensive)
    def _update_snapshot_fallback(self, snap: Snapshot) -> None:
        mp = self.monitor_page
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
            from PyQt6.QtWidgets import QProgressBar
            prog = QProgressBar()
            prog.setRange(0, max(1, g.mem_total_mib))
            prog.setValue(g.mem_used_mib)
            prog.setFormat(f"{g.mem_used_mib} / {g.mem_total_mib} MiB")
            mp.gpu_table.setCellWidget(r, 3, prog)
            n_procs = procs_per_uuid.get(g.uuid, 0)
            procs_item = QTableWidgetItem(str(n_procs))
            procs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            mp.gpu_table.setItem(r, 4, procs_item)

    # Remote OS info ------------------------------------------------------
    def _fetch_remote_os(self) -> None:
        hp = self._host_params
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
        mode = 'default'
        conda_env = self.monitor_page.conda_combo.currentText().strip()
        use_docker = bool(self.monitor_page.use_docker_cb.isChecked())
        docker_container = self.monitor_page.docker_combo.currentText().strip()
        script = self.monitor_page.script_edit.text().strip()
        params = []
        t = self.monitor_page.params_table
        for r in range(t.rowCount()):
            k_item = t.item(r, 0); v_item = t.item(r, 1)
            k = (k_item.text() if k_item else "").strip()
            v = (v_item.text() if v_item else "").strip()
            if k:
                params.append([k, v])
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
            "script": script,
            "params": params,
            "env": env,
        }

    def _build_python_cmd(self, runner: Dict[str, Any]) -> str:
        script = runner.get("script") or ""
        # Decide torch.distributed.run based on console GPU selection (>1) or explicit flag
        gpu_list = runner.get('gpu_list') or []
        try:
            nproc = int(len(gpu_list)) if isinstance(gpu_list, (list, tuple)) else 0
        except Exception:
            nproc = 0
        use_torchrun = bool(nproc > 1 or runner.get('use_torchrun', False))

        parts: list[str] = ["python"]
        if use_torchrun:
            parts += ["-m", "torch.distributed.run", "--nnodes=1", f"--nproc_per_node={max(1, nproc)}"]
        if script:
            parts.append(shlex.quote(script))

        # Script params; ensure --distributed present when using torchrun
        have_distributed = False
        for k, v in runner.get("params", []):
            key = str(k).strip()
            if not key:
                continue
            if key == 'distributed' or key == '--distributed':
                have_distributed = True
            if not key.startswith("--"):
                key = "--" + key
            if v is None or v == "":
                parts.append(key)
            else:
                parts.append(f"{key}={shlex.quote(str(v))}")
        if use_torchrun and not have_distributed:
            parts.append("--distributed")
        return " ".join(parts)

    def _update_runner_preview(self) -> None:
        try:
            r = self._collect_runner()
            cmds = self._build_preview_commands(r)
            if hasattr(self.monitor_page, 'set_preview_commands'):
                self.monitor_page.set_preview_commands(cmds)
            else:
                try:
                    self.monitor_page.preview_edit.setPlainText("\n".join(cmds))
                except Exception:
                    pass
        except Exception as e:
            try:
                self._log_debug(f"[ui:error] preview failed: {e}")
            except Exception:
                pass

    def _console_selected_preset(self) -> str:
        try:
            return (self.monitor_page.console_profile_combo.currentText() or "").strip()
        except Exception:
            return ""

    def _console_runner(self) -> Dict[str, Any]:
        """Assemble runner dict for Console tab, overlaying GPU selection.

        Does not alter Runner panel fields or saved config.
        """
        import copy as _copy
        base = None
        name = self._console_selected_preset()
        if name:
            try:
                base = config_store.load_runner_preset(self._config, name)
            except Exception:
                base = None
        if not base:
            base = self._collect_runner()
        r = _copy.deepcopy(base)
        # Overlay CUDA_VISIBLE_DEVICES based on Console GPU selection
        gsel: list[int] = []
        try:
            if hasattr(self.monitor_page, 'get_console_selected_gpus'):
                gsel = list(self.monitor_page.get_console_selected_gpus())
        except Exception:
            gsel = []
        try:
            gsel = sorted({int(x) for x in gsel})
        except Exception:
            gsel = []
        if gsel:
            env_list = list(r.get('env', []))
            env_list = [[k, v] for (k, v) in env_list if str(k).strip().upper() != 'CUDA_VISIBLE_DEVICES']
            env_list.insert(0, ['CUDA_VISIBLE_DEVICES', ','.join(str(i) for i in gsel)])
            r['env'] = env_list
        try:
            r['gpu_list'] = gsel
        except Exception:
            pass
        return r

    def _update_console_preview(self) -> None:
        try:
            r = self._console_runner()
            cmds = self._build_preview_commands(r)
            try:
                if hasattr(self.monitor_page, 'set_container_shell_command'):
                    self.monitor_page.set_container_shell_command(self._container_enter_cmd(r))
            except Exception:
                pass
            if hasattr(self.monitor_page, 'set_console_preview_commands'):
                self.monitor_page.set_console_preview_commands(cmds)
        except Exception:
            pass

    def _set_last_runner_preset(self, name: str) -> None:
        try:
            name = (name or '').strip()
            if not name:
                return
            self._config["last_runner_preset"] = name
            config_store.save_config(self._config)
        except Exception:
            pass

    def _sync_console_preset_from_runner(self) -> None:
        try:
            name = (self.monitor_page.preset_combo.currentText() or '').strip()
            if not name:
                return
            cb = self.monitor_page.console_profile_combo
            idx = cb.findText(name)
            if idx >= 0:
                cb.setCurrentIndex(idx)
                self._update_console_preview()
        except Exception:
            pass

    def _build_preview_commands(self, r: Dict[str, Any]) -> list[str]:
        import shlex as _sh
        env_dict = {k: v for k, v in r.get("env", [])}
        use_docker = bool(r.get("use_docker"))
        docker_container = (r.get("docker_container") or "").strip()
        conda_env = "" if use_docker else (r.get("conda_env") or "").strip()

        base_py = self._build_python_cmd(r)

        def _env_inline_prefix() -> str:
            pairs = []
            for k, v in env_dict.items():
                k = str(k).strip()
                if not k:
                    continue
                pairs.append(f"{k}={_sh.quote(str(v))}")
            return " ".join(pairs)

        def _conda_line() -> str:
            if not conda_env:
                return ""
            env_q = _sh.quote(conda_env)
            return (
                "for p in \"$HOME/miniconda3/etc/profile.d/conda.sh\" \"$HOME/anaconda3/etc/profile.d/conda.sh\" /opt/conda/etc/profile.d/conda.sh; "
                "do [ -f \"$p\" ] && . \"$p\" && break; done; "
                f"ENV={env_q}; case \"$ENV\" in miniconda3|anaconda3|miniforge3|mambaforge|micromamba|\"\") ENV=base;; esac; "
                "conda activate \"$ENV\" 2>/dev/null || conda activate base 2>/dev/null"
            )

        cmds: list[str] = []
        if use_docker and docker_container:
            cmds += [f"docker exec -it {docker_container} bash -l"]
            env_prefix = _env_inline_prefix()
            py_line = f"{env_prefix} {base_py}" if env_prefix else base_py
            cmds.append(py_line)
            return cmds
        if use_docker and not docker_container:
            cmds += ["docker exec -it <container> bash -l"]
            env_prefix = _env_inline_prefix()
            py_line = f"{env_prefix} {base_py}" if env_prefix else base_py
            cmds.append(py_line)
            return cmds
        cl = _conda_line()
        if cl:
            cmds.append(cl)
        env_prefix = _env_inline_prefix()
        py_line = f"{env_prefix} {base_py}" if env_prefix else base_py
        cmds.append(py_line)
        return cmds

    def _container_enter_cmd(self, r: Dict[str, Any]) -> str:
        try:
            name = (r.get('docker_container') or '').strip()
            if r.get('use_docker') and name:
                return f"docker exec -it {name} bash -l"
        except Exception:
            pass
        return ""

    def _run_runner(self) -> None:
        hp = self._host_params
        if not hp:
            QMessageBox.warning(self, "Not connected", "Please connect first")
            return
        r = self._collect_runner()
        key = self._host_key()
        if key:
            config_store.save_runner(self._config, key, r["mode"], r)
        env_dict = {k: v for k, v in r.get("env", [])}
        base = self._build_python_cmd(r)
        use_docker = bool(r.get("use_docker"))
        docker_container = (r.get("docker_container") or "").strip()
        if use_docker and docker_container:
            inner = SSHCommandJob.build_inner(env=env_dict, conda_env=None, base_cmd=base, docker_container=docker_container)
        else:
            if use_docker and not docker_container:
                try:
                    self.status.showMessage("Docker 勾选但未选容器，将在主机上运行", 5000)
                except Exception:
                    pass
            inner = SSHCommandJob.build_inner(env=env_dict, conda_env=(r.get("conda_env") or None), base_cmd=base)

        job = SSHCommandJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"), inner)
        try:
            self.status.showMessage("Run started…", 3000)
        except Exception:
            pass
        try:
            self.monitor_page.run_btn.setEnabled(False)
        except Exception:
            pass
        job.line.connect(lambda s: (sys.stdout.write(s.rstrip("\n")+"\n"), sys.stdout.flush()))
        job.error.connect(lambda m: (sys.stdout.write(("[error] "+(m or "")).rstrip("\n")+"\n"), sys.stdout.flush()))
        def _done(rc: int) -> None:
            try:
                self.status.showMessage(f"Run finished (rc={rc})", 5000)
            except Exception:
                pass
            try:
                self.monitor_page.run_btn.setEnabled(True)
            except Exception:
                pass
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
        hp = self._host_params
        if from_click:
            try:
                sys.stdout.write("[ui] Refresh envs clicked at %s\n" % time.strftime('%H:%M:%S'))
                sys.stdout.write("[conda-detect] host=%s user=%s port=%s\n" % (hp.get('host'), hp.get('username'), hp.get('port')))
                sys.stdout.flush()
            except Exception:
                pass
        if not hp:
            if from_click:
                try:
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
                sys.stdout.write(m + "\n"); sys.stdout.flush()
            except Exception:
                pass
        try:
            job.debug.connect(_dbg2)
        except Exception:
            pass
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

    # Graceful shutdown of threads to avoid 'QThread destroyed while running'
    def _graceful_shutdown(self) -> None:
        # Stop reverse tunnel
        try:
            if self._reverse_tunnel is not None:
                self._reverse_tunnel.stop_tunnel()
                try:
                    self._reverse_tunnel.wait(2000)
                except Exception:
                    pass
                self._reverse_tunnel = None
        except Exception:
            pass
        # Stop console shells
        try:
            for sh in list(self._console_shells.values()):
                try:
                    sh.stop_shell()
                except Exception:
                    pass
                try:
                    sh.wait(1500)
                except Exception:
                    pass
            self._console_shells.clear()
        except Exception:
            pass
        # Stop poller
        try:
            if self._poller is not None:
                self._poller.stop()
                try:
                    self._poller.wait(1500)
                except Exception:
                    pass
                self._poller = None
        except Exception:
            pass
        # Stop background jobs
        try:
            for t in list(self._bg_jobs):
                try:
                    if hasattr(t, 'requestInterruption'):
                        t.requestInterruption()
                except Exception:
                    pass
                try:
                    t.quit()
                except Exception:
                    pass
                try:
                    t.wait(1000)
                except Exception:
                    pass
            self._bg_jobs.clear()
        except Exception:
            pass
        # Stop test threads
        try:
            for t in list(self._test_threads):
                try:
                    if hasattr(t, 'requestInterruption'):
                        t.requestInterruption()
                except Exception:
                    pass
                try:
                    t.quit()
                except Exception:
                    pass
                try:
                    t.wait(1000)
                except Exception:
                    pass
            self._test_threads.clear()
        except Exception:
            pass

    # Ensure shutdown on window close
    def closeEvent(self, ev):  # type: ignore[override]
        try:
            self._graceful_shutdown()
        except Exception:
            pass
        try:
            super().closeEvent(ev)
        except Exception:
            pass


    def _on_conda_envs(self, envs: list[str]) -> None:
        try:
            cb = self.monitor_page.conda_combo
            cur = cb.currentText().strip()
            cb.blockSignals(True)
            cb.clear()
            for n in envs:
                cb.addItem(n)
            if cur:
                cb.setCurrentText(cur)
            elif envs:
                pick = None
                for pref in ("isaaclab", "isaac", "base"):
                    if pref in envs:
                        pick = pref; break
                cb.setCurrentText(pick or envs[0])
            cb.blockSignals(False)
            self._update_runner_preview()
        except Exception:
            pass

    def _detect_remote_docker_containers(self, from_click: bool = False) -> None:
        hp = self._host_params
        if not hp:
            return
        from .ssh_exec import DockerContainerListJob
        job = DockerContainerListJob(hp["host"], int(hp["port"]), hp.get("username"), hp.get("identity"), hp.get("password"))
        def _set(names: list[str]) -> None:
            try:
                cb = self.monitor_page.docker_combo
                cur = cb.currentText().strip()
                cb.blockSignals(True)
                cb.clear()
                for n in names:
                    cb.addItem(n)
                if cur:
                    cb.setCurrentText(cur)
                cb.blockSignals(False)
                self._update_runner_preview()
            except Exception:
                pass
        job.result.connect(_set)
        def _err(m: str) -> None:
            try:
                self.status.showMessage(m or "docker ps failed", 5000)
            except Exception:
                pass
        job.error.connect(_err)
        def _dbg(m: str) -> None:
            try:
                sys.stdout.write(m.rstrip("\n") + "\n"); sys.stdout.flush()
            except Exception:
                pass
        try:
            job.debug.connect(_dbg)
        except Exception:
            pass
        job.setParent(self)
        job.start()


    # No separate local target; use host 127.0.0.1 if needed

    def _apply_runner_fields(self, r: Dict[str, Any]) -> None:
        try:
            self.monitor_page.conda_combo.setCurrentText(r.get("conda_env", ""))
            self.monitor_page.use_docker_cb.setChecked(bool(r.get("use_docker", False)))
            self.monitor_page.docker_combo.setCurrentText(r.get("docker_container", ""))
            self.monitor_page.script_edit.setText(r.get("script", ""))
            self.monitor_page.params_table.setRowCount(0)
            for k, v in r.get("params", []):
                row = self.monitor_page.params_table.rowCount()
                self.monitor_page.params_table.insertRow(row)
                self.monitor_page.params_table.setItem(row, 0, QTableWidgetItem(str(k)))
                self.monitor_page.params_table.setItem(row, 1, QTableWidgetItem(str(v)))
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
            last = (self._config.get("last_runner_preset") or "").strip()
            if last and last in names:
                cb.setCurrentText(last)
            elif cur:
                cb.setCurrentText(cur)
            cb.blockSignals(False)
        except Exception:
            pass
        # Keep Console preset in sync even if we suppressed runner signals above
        try:
            cc = self.monitor_page.console_profile_combo
            cc.blockSignals(True)
            cc.setCurrentText(self.monitor_page.preset_combo.currentText())
            cc.blockSignals(False)
        except Exception:
            pass
        try:
            self.monitor_page.update_console_preset_visibility()
            # Refresh Console preview to reflect selected preset
            self._update_console_preview()
        except Exception:
            pass

    def _save_preset(self) -> None:
        name = self.monitor_page.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Please input a preset name")
            return
        r = self._collect_runner()
        config_store.save_runner_preset(self._config, name, r)
        try:
            self._config["last_runner_preset"] = name
            config_store.save_config(self._config)
        except Exception:
            pass
        try:
            self._autosave_runner(r)
        except Exception:
            pass
        self.status.showMessage(f"Saved preset '{name}'")
        self._refresh_presets()

    def _load_preset_into_ui(self) -> None:
        name = self.monitor_page.preset_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Please input a preset name")
            return
        r = config_store.load_runner_preset(self._config, name)
        try:
            self._set_last_runner_preset(name)
        except Exception:
            pass
        try:
            config_store.save_config(self._config)
        except Exception:
            pass
        self._apply_runner_fields(r)
        self.status.showMessage(f"Loaded preset '{name}' into UI", 4000)
        try:
            if (self.monitor_page.console_profile_combo.currentText() or '').strip() == name:
                self._update_console_preview()
        except Exception:
            pass

    def _delete_preset(self) -> None:
        name = self.monitor_page.preset_combo.currentText().strip()
        if not name:
            return
        from PyQt6.QtWidgets import QMessageBox as _QB
        resp = _QB.question(self, "Delete preset", f"Delete preset '{name}'?")
        if resp == _QB.StandardButton.Yes:
            config_store.delete_runner_preset(self._config, name)
            try:
                if self._config.get("last_runner_preset") == name:
                    self._config["last_runner_preset"] = ""
                    config_store.save_config(self._config)
            except Exception:
                pass
            self._refresh_presets()
            self.status.showMessage(f"Deleted preset '{name}'", 4000)
            try:
                if (self.monitor_page.console_profile_combo.currentText() or '').strip() == name:
                    self._update_console_preview()
            except Exception:
                pass

    # Runner autosave -----------------------------------------------------
    def _autosave_runner(self, runner: Optional[Dict[str, Any]] = None) -> None:
        try:
            r = runner or self._collect_runner()
            key = self._host_key()
            if key:
                config_store.save_runner(self._config, key, r["mode"], r)
        except Exception:
            pass
