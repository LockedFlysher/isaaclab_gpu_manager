from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from .nvidia_parser import summarize, GpuInfo, ComputeApp, parse_pmon


@dataclass
class Snapshot:
    t_unix: float
    gpus: List[GpuInfo]
    apps: List[ComputeApp]
    user_vram_mib: Dict[str, int]
    raw_errors: List[str]
    pid_user_map: Dict[int, str] = None  # pid -> user (filled when available)


class SSHGpuPoller(QThread):
    """Worker thread that polls a remote server via ssh to fetch GPU metrics.

    Two modes:
      - "ssh" subprocess mode (default): uses local ssh binary, BatchMode.
      - "paramiko" mode: used when a password is provided; maintains a persistent
        SSH connection and runs commands via exec_command.
    """

    snapshot_ready = pyqtSignal(object)  # emits Snapshot
    error_msg = pyqtSignal(str)

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: Optional[str] = None,
        password: Optional[str] = None,
        identity_file: Optional[str] = None,
        interval_sec: float = 5.0,
        ssh_bin: str = "ssh",
        timeout_sec: float = 8.0,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._username = username
        self._password = password
        self._identity = identity_file
        self._interval = float(interval_sec)
        self._ssh_bin = ssh_bin
        self._timeout = float(timeout_sec)
        self._stop = False
        self._pmk_client = None
        self._use_paramiko = password is not None

    def stop(self) -> None:
        self._stop = True

    # Internal helpers -----------------------------------------------------
    def _ssh_base(self) -> List[str]:
        dest = f"{self._username}@{self._host}" if self._username else self._host
        cmd = [self._ssh_bin, "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        cmd.append(dest)
        return cmd

    def _run_remote(self, remote_cmd: str) -> Tuple[int, str, str]:
        if self._use_paramiko:
            return self._pmk_run(remote_cmd)
        cmd = self._ssh_base() + ["--", "bash", "-lc", remote_cmd]
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "ssh command timed out"
        except Exception as e:  # noqa: BLE001 - broad ok here
            return 1, "", f"ssh error: {e}"

    # Paramiko helpers ----------------------------------------------------
    def _pmk_connect(self) -> Optional[str]:
        try:
            import paramiko  # type: ignore
        except Exception:
            return "paramiko not installed; please pip install paramiko"
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
            self._pmk_client = client
            return None
        except Exception as e:  # noqa: BLE001
            self._pmk_client = None
            msg = str(e)
            if "Error reading SSH protocol banner" in msg:
                msg += \
                    "; tip: check host/port, firewall, or increase banner timeout; " \
                    "verify the server runs SSH on this port"
            return msg

    def _pmk_run(self, remote_cmd: str) -> Tuple[int, str, str]:
        if self._pmk_client is None:
            err = self._pmk_connect()
            if err:
                return 1, "", err
        try:
            # Run with bash -lc to get login-shell semantics
            cmd = f"bash -lc {shlex.quote(remote_cmd)}"
            stdin, stdout, stderr = self._pmk_client.exec_command(cmd, timeout=self._timeout)  # type: ignore[union-attr]
            out = stdout.read().decode(errors="ignore")
            err_s = stderr.read().decode(errors="ignore")
            rc = stdout.channel.recv_exit_status()  # type: ignore[attr-defined]
            return rc, out, err_s
        except Exception as e:  # noqa: BLE001
            # Try reconnect once on failure
            err = self._pmk_connect()
            if err:
                return 1, "", f"reconnect failed: {err}"
            try:
                cmd = f"bash -lc {shlex.quote(remote_cmd)}"
                stdin, stdout, stderr = self._pmk_client.exec_command(cmd, timeout=self._timeout)  # type: ignore[union-attr]
                out = stdout.read().decode(errors="ignore")
                err_s = stderr.read().decode(errors="ignore")
                rc = stdout.channel.recv_exit_status()  # type: ignore[attr-defined]
                return rc, out, err_s
            except Exception as e2:  # noqa: BLE001
                return 1, "", f"ssh error: {e2}"

    def _fetch_cycle(self) -> Snapshot:
        errors: List[str] = []

        rc1, out_gpus, err1 = self._run_remote(
            "nvidia-smi --query-gpu=index,name,uuid,utilization.gpu,memory.total,memory.used --format=csv,noheader,nounits"
        )
        if rc1 != 0:
            errors.append(err1.strip() or f"gpu query failed rc={rc1}")
            # Keep going; out_gpus may be empty.

        rc2, out_apps, err2 = self._run_remote(
            "nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits || true"
        )
        if rc2 != 0 and err2:
            errors.append(err2.strip())

        # Extract pids for ps
        pids: List[str] = []
        for line in out_apps.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                pid = parts[1]
                if pid.isdigit():
                    pids.append(pid)

        out_ps = ""
        pid_user_map: Dict[int, str] = {}
        if pids:
            pid_arg = ",".join(pids)
            rc3, out_ps, err3 = self._run_remote(f"ps -o pid=,user= -p {shlex.quote(pid_arg)}")
            if rc3 != 0 and err3:
                errors.append(err3.strip())
        if out_ps:
            from .nvidia_parser import parse_ps_pid_user as _parse_ps
            pid_user_map.update(_parse_ps(out_ps))

        gpus, apps, user_totals = summarize(out_gpus, out_apps, out_ps)

        # Fallback: if no compute-apps, try pmon to estimate per-proc VRAM
        if not apps:
            rc4, out_pmon, err4 = self._run_remote("nvidia-smi pmon -c 1 || true")
            if rc4 != 0 and err4:
                errors.append(err4.strip())
            pmon_rows = parse_pmon(out_pmon)
            if pmon_rows:
                # Build pid->user and user totals from pmon
                # Prepare uuid map by index
                idx_to_uuid = {gi.index: gi.uuid for gi in summarize(out_gpus, "", "")[0]}
                # Collect pids for ps (again, as pmon may include more pids)
                pids2 = [str(pid) for (_, pid, _, _) in pmon_rows]
                out_ps2 = ""
                if pids2:
                    pid_arg2 = ",".join(pids2)
                    rc5, out_ps2, err5 = self._run_remote(f"ps -o pid=,user= -p {pid_arg2}")
                    if rc5 != 0 and err5:
                        errors.append(err5.strip())
                pid_map2 = {}
                # Merge maps: prefer new mapping, fallback to previous mapping
                from .nvidia_parser import parse_ps_pid_user
                pid_map2 = parse_ps_pid_user(out_ps2)
                # Build ComputeApp list and user totals
                apps = []
                user_totals = {}
                for gpu_idx, pid, proc_name, fb_mib in pmon_rows:
                    uuid = idx_to_uuid.get(gpu_idx, str(gpu_idx))
                    apps.append(ComputeApp(gpu_uuid=uuid, pid=pid, process_name=proc_name, used_memory_mib=fb_mib))
                    user = pid_map2.get(pid, "unknown")
                    user_totals[user] = user_totals.get(user, 0) + max(0, fb_mib)
                pid_user_map = pid_map2
        # Ensure map exists
        pid_user_map = pid_user_map or {}
        snap = Snapshot(time.time(), gpus, apps, user_totals, errors)
        try:
            snap.pid_user_map = pid_user_map
        except Exception:
            pass
        return snap

    # QThread --------------------------------------------------------------
    def run(self) -> None:  # noqa: D401 - QThread run
        # If paramiko mode, connect once up-front
        if self._use_paramiko:
            err = self._pmk_connect()
            if err:
                self.error_msg.emit(err)
        while not self._stop and not self.isInterruptionRequested():
            snap = self._fetch_cycle()
            if snap.raw_errors:
                self.error_msg.emit("; ".join(snap.raw_errors))
            self.snapshot_ready.emit(snap)
            # Sleep in small steps to react faster to stop
            slept = 0.0
            step = 0.1
            while slept < self._interval and not self._stop and not self.isInterruptionRequested():
                time.sleep(step)
                slept += step

