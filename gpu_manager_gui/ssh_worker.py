from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from .nvidia_parser import summarize, GpuInfo, ComputeApp


@dataclass
class Snapshot:
    t_unix: float
    gpus: List[GpuInfo]
    apps: List[ComputeApp]
    user_vram_mib: Dict[str, int]
    raw_errors: List[str]


class SSHGpuPoller(QThread):
    """Worker thread that polls a remote server via ssh to fetch GPU metrics.

    It uses the local `ssh` binary to avoid extra Python dependencies. Three
    simple commands are executed each cycle:
      1) GPU summary
      2) Compute apps
      3) ps pid->user mapping (only if there are compute apps)
    """

    snapshot_ready = pyqtSignal(object)  # emits Snapshot
    error_msg = pyqtSignal(str)

    def __init__(
        self,
        host: str,
        port: int = 22,
        identity_file: Optional[str] = None,
        interval_sec: float = 5.0,
        ssh_bin: str = "ssh",
        timeout_sec: float = 8.0,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._identity = identity_file
        self._interval = float(interval_sec)
        self._ssh_bin = ssh_bin
        self._timeout = float(timeout_sec)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # Internal helpers -----------------------------------------------------
    def _ssh_base(self) -> List[str]:
        cmd = [self._ssh_bin, "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        cmd.append(self._host)
        return cmd

    def _run_remote(self, remote_cmd: str) -> Tuple[int, str, str]:
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
        if pids:
            pid_arg = ",".join(pids)
            rc3, out_ps, err3 = self._run_remote(f"ps -o pid=,user= -p {shlex.quote(pid_arg)}")
            if rc3 != 0 and err3:
                errors.append(err3.strip())

        gpus, apps, user_totals = summarize(out_gpus, out_apps, out_ps)
        return Snapshot(time.time(), gpus, apps, user_totals, errors)

    # QThread --------------------------------------------------------------
    def run(self) -> None:  # noqa: D401 - QThread run
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
