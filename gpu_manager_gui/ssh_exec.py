from __future__ import annotations

import shlex
import subprocess
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal


def _compose_inner_command(env: Dict[str, str], conda_env: Optional[str], base_cmd: str) -> str:
    # Build environment prefix (KEY=VAL ...) with proper quoting
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) if env else ""
    cmd = base_cmd
    if conda_env:
        # Prefer conda run; fallback to activation via conda.sh
        run = f"conda run -n {shlex.quote(conda_env)} --no-capture-output {cmd}"
        fallback = (
            f"(source ~/.bashrc >/dev/null 2>&1 || true); "
            f"(source ~/miniconda3/etc/profile.d/conda.sh >/dev/null 2>&1 || true); "
            f"(source ~/anaconda3/etc/profile.d/conda.sh >/dev/null 2>&1 || true); "
            f"(source /opt/conda/etc/profile.d/conda.sh >/dev/null 2>&1 || true); "
            f"conda activate {shlex.quote(conda_env)} && {cmd}"
        )
        cmd = f"{run} || ( {fallback} )"
    if exports:
        cmd = f"{exports} {cmd}"
    return cmd


class SSHCommandJob(QThread):
    line = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(
        self,
        host: str,
        port: int,
        username: Optional[str],
        identity: Optional[str],
        password: Optional[str],
        inner_command: str,
        timeout: float = 0.0,
    ) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._user = username
        self._identity = identity
        self._password = password
        self._inner_cmd = inner_command
        self._timeout = float(timeout or 0.0)

    @staticmethod
    def build_inner(env: Dict[str, str], conda_env: Optional[str], base_cmd: str) -> str:
        return _compose_inner_command(env, conda_env, base_cmd)

    def run(self) -> None:  # type: ignore[override]
        if self._password:
            try:
                import paramiko  # type: ignore
            except Exception:
                self.error.emit("Paramiko not installed; cannot run password-based SSH command")
                self.finished.emit(1)
                return
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self._host,
                    port=self._port,
                    username=self._user,
                    password=self._password,
                    key_filename=self._identity,
                    timeout=10.0,
                    banner_timeout=15.0,
                    auth_timeout=15.0,
                    allow_agent=True,
                    look_for_keys=True,
                )
                cmd = f"bash -lc {shlex.quote(self._inner_cmd)}"
                transport = client.get_transport()
                chan = transport.open_session()  # type: ignore[union-attr]
                chan.exec_command(cmd)
                # Stream output
                import select
                while True:
                    if chan.exit_status_ready():
                        while chan.recv_ready():
                            data = chan.recv(4096).decode(errors="ignore")
                            if data:
                                self.line.emit(data)
                        while chan.recv_stderr_ready():
                            data = chan.recv_stderr(4096).decode(errors="ignore")
                            if data:
                                self.line.emit(data)
                        break
                    r, _, _ = select.select([chan], [], [], 0.2)
                    if r:
                        while chan.recv_ready():
                            data = chan.recv(4096).decode(errors="ignore")
                            if data:
                                self.line.emit(data)
                        while chan.recv_stderr_ready():
                            data = chan.recv_stderr(4096).decode(errors="ignore")
                            if data:
                                self.line.emit(data)
                rc = chan.recv_exit_status()
                chan.close()
                client.close()
                self.finished.emit(int(rc))
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))
                self.finished.emit(1)
            return

        # ssh subprocess path
        dest = f"{self._user}@{self._host}" if self._user else self._host
        cmd = [
            "ssh",
            "-p",
            str(self._port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
        ]
        if self._identity:
            cmd += ["-i", self._identity]
        cmd += [dest, "--", "bash", "-lc", self._inner_cmd]

        try:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert p.stdout is not None
            for line in p.stdout:
                self.line.emit(line)
            p.stdout.close()
            rc = p.wait()
            self.finished.emit(int(rc))
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
            self.finished.emit(1)


class CondaEnvListJob(QThread):
    result = pyqtSignal(list)
    error = pyqtSignal(str)
    debug = pyqtSignal(str)

    def __init__(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str]) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._user = username
        self._identity = identity
        self._password = password

    def _run_remote(self, inner: str) -> Tuple[int, str, str]:
        dest = f"{self._user}@{self._host}" if self._user else self._host
        cmd = ["ssh", "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        cmd += [dest, "--", "bash", "-lc", inner]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            return p.returncode, p.stdout, p.stderr
        except Exception as e:  # noqa: BLE001
            return 1, "", str(e)

    def run(self) -> None:  # type: ignore[override]
        # Build robust detection script: source common conda.sh locations, then try JSON, then text, finally list envs directories
        detect_script = (
            "echo '[conda-detect] start' 1>&2; "
            "(command -v conda >/dev/null 2>&1 && echo '[conda-detect] conda found in PATH' 1>&2) || "
            "(echo '[conda-detect] sourcing common conda.sh locations' 1>&2; "
            " source ~/.bashrc >/dev/null 2>&1 || true; "
            " for p in ~/miniconda3/etc/profile.d/conda.sh ~/anaconda3/etc/profile.d/conda.sh /opt/conda/etc/profile.d/conda.sh; do "
            "   if [ -f \"$p\" ]; then echo \"[conda-detect] source $p\" 1>&2; source \"$p\" >/dev/null 2>&1; break; fi; done; "
            " eval \"$(conda shell.bash hook 2>/dev/null)\" || true); "
            "(conda env list --json 2>/dev/null) || (conda info --envs 2>/dev/null) || "
            "(ls -1d ~/miniconda3/envs/* ~/anaconda3/envs/* /opt/conda/envs/* 2>/dev/null | xargs -n1 basename 2>/dev/null | sort -u || true)"
        )

        if self._password:
            try:
                import paramiko  # type: ignore
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self._host,
                    port=self._port,
                    username=self._user,
                    password=self._password,
                    key_filename=self._identity,
                    timeout=10.0,
                    banner_timeout=15.0,
                    auth_timeout=15.0,
                    allow_agent=True,
                    look_for_keys=True,
                )
                cmd = f"bash -lc {shlex.quote(detect_script)}"
                stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
                out = stdout.read().decode(errors="ignore")
                err = stderr.read().decode(errors="ignore")
                client.close()
                if err:
                    self.debug.emit(err)
                envs = self._parse_envs(out)
                self.result.emit(envs)
                return
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))
                self.result.emit([])
                return

        rc, out, err = self._run_remote(detect_script)
        if rc != 0 and err:
            self.debug.emit(err)
        envs = self._parse_envs(out)
        self.result.emit(envs)

    @staticmethod
    def _parse_envs(out: str) -> List[str]:
        out = out.strip()
        if not out:
            return []
        # Try JSON first
        if out.startswith("{"):
            try:
                import json
                data = json.loads(out)
                # conda env list --json has keys: envs (list of paths)
                paths = data.get("envs", [])
                names: List[str] = []
                for p in paths:
                    if not isinstance(p, str):
                        continue
                    name = p.split("/")[-1] or p
                    names.append(name)
                return names
            except Exception:
                pass
        # Fallback parse conda info --envs text or plain names
        envs: List[str] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # handle 'name * path' or just 'name'
            if line.endswith("*"):
                line = line[:-1].strip()
            parts = line.split()
            if len(parts) >= 1:
                envs.append(parts[0])
        return envs
