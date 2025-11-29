from __future__ import annotations

import shlex
import subprocess
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal
import re


def _compose_inner_command(env: Dict[str, str], conda_env: Optional[str], base_cmd: str, docker_container: Optional[str] = None) -> str:
    # Build environment prefix (KEY=VAL ...) with proper quoting
    exports = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) if env else ""
    cmd = base_cmd
    # If docker is selected, wrap with docker exec; conda settings are ignored in this case
    if docker_container:
        # Robust python fallback inside container: try python, then python3, then conda run
        # Keep the original python invocation as PY_CALL, and replace leading 'python ' with the detected interpreter.
        import shlex as _sh
        py_call_q = _sh.quote(cmd)
        env_prefix_q = _sh.quote(exports) if exports else ""
        # Prefer user-chosen conda env inside container if given, else try 'base'
        conda_name = (conda_env or "base")
        inner_script = (
            "PY_CALL=" + py_call_q + "; "
            "ENV_PREFIX=" + (env_prefix_q or "\"\"") + "; "
            # Try python variants first
            "ok=0; for PY in python python3 /usr/bin/python3 /usr/local/bin/python3; do "
            "  if command -v \"$PY\" >/dev/null 2>&1; then "
            "    CMD=\"$PY_CALL\"; CMD=\"${CMD/#python /$PY }\"; "
            "    if [ -n \"$ENV_PREFIX\" ]; then eval \"$ENV_PREFIX $CMD\"; else eval \"$CMD\"; fi; ok=1; break; "
            "  fi; "
            "done; "
            # Try conda run inside the container
            "if [ \"$ok\" -eq 0 ]; then "
            "  (source ~/.bashrc >/dev/null 2>&1 || true); "
            "  for p in ~/miniconda3/etc/profile.d/conda.sh ~/anaconda3/etc/profile.d/conda.sh /opt/conda/etc/profile.d/conda.sh; do [ -f \"$p\" ] && . \"$p\" >/dev/null 2>&1 && break; done; "
            "  if command -v conda >/dev/null 2>&1; then "
            "    CMD=\"$PY_CALL\"; CMD=\"${CMD/#python /python }\"; "
            "    if [ -n \"$ENV_PREFIX\" ]; then eval \"$ENV_PREFIX conda run -n " + _sh.quote(conda_name) + " --no-capture-output $CMD\"; else eval \"conda run -n " + _sh.quote(conda_name) + " --no-capture-output $CMD\"; fi; ok=1; "
            "  fi; "
            "fi; "
            "if [ \"$ok\" -eq 0 ]; then echo '[docker-run] python not found in container' 1>&2; exit 127; fi"
        )
        # Use login shell (-l) to pick up /etc/profile and system PATH adjustments
        return f"docker exec -i {_sh.quote(docker_container)} bash -l -c {_sh.quote(inner_script)}"
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
    def build_inner(env: Dict[str, str], conda_env: Optional[str], base_cmd: str, docker_container: Optional[str] = None) -> str:
        return _compose_inner_command(env, conda_env, base_cmd, docker_container)

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
            # Header
            "echo '[conda-detect] start' 1>&2; "
            "echo '[conda-detect] whoami='$(whoami)' shell='$SHELL 1>&2; "
            # Source common rc files explicitly (bash login shells won't source .bashrc by default)
            "if [ -f $HOME/.bashrc ]; then echo '[conda-detect] source ~/.bashrc' 1>&2; . $HOME/.bashrc >/dev/null 2>&1; fi; "
            "if [ -f $HOME/.bash_profile ]; then echo '[conda-detect] source ~/.bash_profile' 1>&2; . $HOME/.bash_profile >/dev/null 2>&1; fi; "
            "if [ -f $HOME/.profile ]; then echo '[conda-detect] source ~/.profile' 1>&2; . $HOME/.profile >/dev/null 2>&1; fi; "
            # Ensure PATH contains common conda bin locations (no literal quotes in PATH)
            "export PATH=\"$HOME/miniconda3/bin:$HOME/anaconda3/bin:$HOME/miniforge3/bin:/opt/conda/bin:$HOME/mambaforge/bin:$HOME/micromamba/bin:$PATH\"; "
            "echo '[conda-detect] PATH='$PATH 1>&2; "
            # Try to source conda.sh from common locations
            "for p in $HOME/miniconda3/etc/profile.d/conda.sh $HOME/anaconda3/etc/profile.d/conda.sh $HOME/miniforge3/etc/profile.d/conda.sh /opt/conda/etc/profile.d/conda.sh $HOME/mambaforge/etc/profile.d/conda.sh $HOME/micromamba/etc/profile.d/conda.sh; do "
            "  if [ -f \"$p\" ]; then echo \"[conda-detect] source $p\" 1>&2; . \"$p\" >/dev/null 2>&1; break; fi; done; "
            # As a fallback, try the appropriate shell hook
            "if [ \"${SHELL##*/}\" = \"zsh\" ]; then eval \"$(conda shell.zsh hook 2>/dev/null)\" >/dev/null 2>&1 || true; else eval \"$(conda shell.bash hook 2>/dev/null)\" >/dev/null 2>&1 || true; fi; "
            # Prepare output accumulator and try detection via conda/mamba
            "ENV_OUT=\"\"; "
            "CONDACMD=$(command -v conda 2>/dev/null || true); "
            "if [ -n \"$CONDACMD\" ]; then echo '[conda-detect] conda='$(command -v conda) 1>&2; ENV_OUT=\"$($CONDACMD env list --json 2>/dev/null || $CONDACMD info --envs 2>/dev/null || true)\"; "
            "else ENV_OUT=\"$(mamba env list --json 2>/dev/null || micromamba env list --json 2>/dev/null || true)\"; fi; "
            # Try zsh login context if conda is configured only in zsh
            "if [ -z \"$ENV_OUT\" ] && command -v zsh >/dev/null 2>&1; then ENV_OUT=\"$(zsh -lc 'CONDACMD=$(command -v conda 2>/dev/null || true); if [ -n \"$CONDACMD\" ]; then $CONDACMD env list --json 2>/dev/null || $CONDACMD info --envs 2>/dev/null || true; fi' 2>/dev/null || true)\"; fi; "
            # Final fallback: only if previous attempts yielded nothing
            "if [ -z \"$ENV_OUT\" ]; then ENV_OUT=\"$(ls -1d $HOME/miniconda3/envs/* $HOME/anaconda3/envs/* $HOME/miniforge3/envs/* $HOME/.conda/envs/* /opt/conda/envs/* $HOME/mambaforge/envs/* 2>/dev/null | xargs -n1 basename 2>/dev/null | sort -u || true)\"; fi; "
            # Print the accumulated output to stdout (JSON or text)
            "printf %s \"$ENV_OUT\""
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
                stdin, stdout, stderr = client.exec_command(cmd, timeout=20)
                out = stdout.read().decode(errors="ignore")
                err = stderr.read().decode(errors="ignore")
                client.close()
                if err:
                    self.debug.emit(err)
                if out:
                    self.debug.emit(out)
                envs = self._parse_envs(out)
                try:
                    self.debug.emit("[conda-detect] parsed envs: %d -> %s" % (len(envs), ", ".join(envs)))
                except Exception:
                    pass
                self.result.emit(envs)
                return
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))
                self.result.emit([])
                return

        rc, out, err = self._run_remote(detect_script)
        if rc != 0 and err:
            self.debug.emit(err)
        # Also emit stdout when debugging to help diagnosis
        if out:
            self.debug.emit(out)
        envs = self._parse_envs(out)
        try:
            self.debug.emit("[conda-detect] parsed envs: %d -> %s" % (len(envs), ", ".join(envs)))
        except Exception:
            pass
        self.result.emit(envs)

    @staticmethod
    def _parse_envs(out: str) -> List[str]:
        out = (out or "").strip()
        if not out:
            return []
        # Try JSON first. Some setups print warnings before JSON; locate the first '{'.
        try:
            json_start = out.index("{")
        except ValueError:
            json_start = -1
        if json_start >= 0:
            try:
                import json
                data = json.loads(out[json_start:])
                # conda env list/info --json typically has key 'envs'; some tools use 'environments'.
                paths = data.get("envs", []) or data.get("environments", [])
                names: List[str] = []
                for p in paths:
                    if not isinstance(p, str):
                        continue
                    # Normalize both unix/windows path separators
                    name = p.replace("\\", "/").split("/")[-1] or p
                    names.append(name)
                return names
            except Exception:
                # If JSON parse fails, fall back to text parsing below
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


class RemoteOSInfoJob(QThread):
    """Fetch remote OS info (pretty name, kernel, hostname) via SSH and emit a one-line summary."""
    result = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str]) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._user = username
        self._identity = identity
        self._password = password

    def run(self) -> None:  # type: ignore[override]
        script = (
            "name=\"\"; "
            "if [ -r /etc/os-release ]; then . /etc/os-release >/dev/null 2>&1; name=\"$PRETTY_NAME\"; fi; "
            "if [ -z \"$name\" ] && command -v lsb_release >/dev/null 2>&1; then name=\"$(lsb_release -ds 2>/dev/null)\"; fi; "
            "if [ -z \"$name\" ]; then name=\"$(uname -s)\"; fi; "
            "kernel=\"$(uname -r 2>/dev/null)\"; host=\"$(hostname 2>/dev/null)\"; "
            "echo \"$name | kernel $kernel | $host\""
        )
        cmd = f"bash -lc {shlex.quote(script)}"
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
                _, stdout, stderr = client.exec_command(cmd, timeout=10)
                out = stdout.read().decode(errors="ignore").strip()
                err = stderr.read().decode(errors="ignore").strip()
                client.close()
                if out:
                    self.result.emit(out)
                elif err:
                    self.error.emit(err)
                else:
                    self.error.emit("empty os info output")
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))
            return

        dest = f"{self._user}@{self._host}" if self._user else self._host
        ssh_cmd = ["ssh", "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            ssh_cmd += ["-i", self._identity]
        ssh_cmd += [dest, "--", cmd]
        try:
            p = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=12)
            out = (p.stdout or "").strip()
            if out:
                self.result.emit(out)
            else:
                self.error.emit((p.stderr or "os info command failed").strip())
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
            
class DockerContainerListJob(QThread):
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
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return p.returncode, p.stdout, p.stderr
        except Exception as e:  # noqa: BLE001
            return 1, "", str(e)

    def run(self) -> None:  # type: ignore[override]
        script = (
            "echo '[docker-detect] start' 1>&2; "
            "echo '[docker-detect] whoami='$(whoami)' shell='$SHELL 1>&2; "
            # Source rc files to pick up rootless DOCKER_HOST and PATH
            "if [ -f $HOME/.bashrc ]; then . $HOME/.bashrc >/dev/null 2>&1; fi; "
            "if [ -f $HOME/.bash_profile ]; then . $HOME/.bash_profile >/dev/null 2>&1; fi; "
            "if [ -f $HOME/.profile ]; then . $HOME/.profile >/dev/null 2>&1; fi; "
            "export PATH=\"$PATH:/usr/bin:/usr/local/bin\"; echo '[docker-detect] PATH='$PATH 1>&2; "
            # Rootless docker socket fallback
            "if [ -z \"$DOCKER_HOST\" ] && [ -n \"$XDG_RUNTIME_DIR\" ] && [ -S \"$XDG_RUNTIME_DIR/docker.sock\" ]; then export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock; fi; "
            "echo '[docker-detect] DOCKER_HOST='${DOCKER_HOST:-'(default)'} 1>&2; "
            "DOCKERCMD=$(command -v docker 2>/dev/null || true); if [ -z \"$DOCKERCMD\" ] && [ -x /usr/bin/docker ]; then DOCKERCMD=/usr/bin/docker; fi; "
            # names + ids to be safe; prefer names; try both direct and sudo (no password) and merge; also try -a
            "OUT1=\"\"; OUT2=\"\"; OUT3=\"\"; OUT4=\"\"; "
            "if [ -n \"$DOCKERCMD\" ]; then OUT1=\"$($DOCKERCMD ps --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; OUT3=\"$($DOCKERCMD ps -a --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; fi; "
            "OUT2=\"$(sudo -n docker ps --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; OUT4=\"$(sudo -n docker ps -a --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; "
            "printf '%s\n%s\n%s\n%s\n' \"$OUT1\" \"$OUT2\" \"$OUT3\" \"$OUT4\" | awk 'NF' | sort -u"
        )
        if self._password:
            try:
                import paramiko  # type: ignore
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    hostname=self._host, port=self._port, username=self._user, password=self._password,
                    key_filename=self._identity, timeout=10.0, banner_timeout=15.0, auth_timeout=15.0,
                    allow_agent=True, look_for_keys=True,
                )
                cmd = f"bash -lc {shlex.quote(script)}"
                _, stdout, stderr = client.exec_command(cmd, timeout=12)
                out = stdout.read().decode(errors="ignore")
                err = stderr.read().decode(errors="ignore")
                client.close()
                if err:
                    self.debug.emit(err)
                if out:
                    self.debug.emit(out)
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))
                self.result.emit([])
                return
        else:
            rc, out, err = self._run_remote(script)
            if rc != 0 and err:
                self.debug.emit(err)
            if out:
                self.debug.emit(out)
        # Parse out -> list of names (prefer names, fallback to ids)
        names = []
        for line in (out or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            name = parts[0].strip() if parts else line
            if name:
                names.append(name)
        if not names:
            # Also try plain `docker ps --format {{.Names}}` (no tab/ID) as a fallback
            more = []
            try:
                rc2, out2, err2 = self._run_remote("docker ps --format '{{.Names}}' 2>/dev/null || true")
                if out2:
                    self.debug.emit(out2)
                    for ln in out2.splitlines():
                        ln = ln.strip();
                        if ln:
                            more.append(ln)
            except Exception:
                pass
            if more:
                names = sorted(set(more))
        self.result.emit(names)


class SSHInteractiveShell(QThread):
    """Interactive SSH shell with PTY. Emits raw text; supports send/close.

    Designed for a single session per MainWindow. Use write() to send keys.
    """
    data = pyqtSignal(str)
    error = pyqtSignal(str)
    connected = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], *, strip_ansi: bool = False) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._user = username
        self._identity = identity
        self._password = password
        self._client = None
        self._chan = None
        self._stop = False
        self._strip_ansi = bool(strip_ansi)

    def run(self) -> None:  # type: ignore[override]
        try:
            import paramiko  # type: ignore
        except Exception:
            self.error.emit("paramiko not installed; please pip install paramiko")
            self.closed.emit()
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
            chan = client.invoke_shell(term='xterm')
            chan.settimeout(0.2)
            self._client = client
            self._chan = chan
            self.connected.emit()
            # Read loop
            import time
            # ANSI/OSC escape filters
            ansi_csi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
            osc = re.compile(r"\x1b\].*?(\x07|\x1b\\)")  # OSC ... BEL or ST
            bracketed_paste = re.compile(r"\x1b\[\?2004[hl]")
            def _clean(s: str) -> str:
                # Drop OSC (title) and bracketed paste toggles
                s = osc.sub("", s)
                s = bracketed_paste.sub("", s)
                # Strip CSI color/control sequences
                s = ansi_csi.sub("", s)
                # Normalize CRLF
                s = s.replace("\r\n", "\n").replace("\r", "\n")
                return s
            while not self._stop:
                try:
                    if chan.recv_ready():
                        data = chan.recv(4096)
                        if not data:
                            break
                        text = data.decode(errors='ignore')
                        if self._strip_ansi:
                            # Backward-compatible cleaning when requested
                            ansi_csi = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
                            osc = re.compile(r'\x1b\].*?(\x07|\x1b\\)')
                            bracketed_paste = re.compile(r'\x1b\\?2004[hl]') if False else re.compile(r'\x1b\[\?2004[hl]')
                            text = osc.sub('', text)
                            text = bracketed_paste.sub('', text)
                            text = ansi_csi.sub('', text)
                            text = text.replace('\r\n', '\n').replace('\r', '\n')
                        self.data.emit(text)
                    else:
                        time.sleep(0.05)
                except Exception:
                    time.sleep(0.05)
            try:
                chan.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
        self.closed.emit()

    def write(self, text: str) -> None:
        try:
            if self._chan is not None:
                self._chan.send(text)
        except Exception:
            pass

    def send_line(self, text: str) -> None:
        self.write(text + "\n")

    def stop_shell(self) -> None:
        self._stop = True
        try:
            if self._chan is not None:
                self._chan.close()
        except Exception:
            pass
    # Allow external widgets to resize remote PTY size
    def resize_pty(self, cols: int, rows: int) -> None:
        try:
            if self._chan is not None:
                self._chan.resize_pty(width=cols, height=rows)
        except Exception:
            pass

class RemoteListDirJob(QThread):
    """List a remote directory via SSH and emit (cwd, entries) where entries is a list of dicts.

    Each entry: { 'name': str, 'type': 'D'|'F'|'O' }
    """
    result = pyqtSignal(str, list)
    error = pyqtSignal(str)

    def __init__(self, host: str, port: int, username: Optional[str], identity: Optional[str], password: Optional[str], path: Optional[str] = None) -> None:
        super().__init__()
        self._host = host
        self._port = int(port)
        self._user = username
        self._identity = identity
        self._password = password
        self._path = path or ""

    def _run_remote(self, inner: str) -> Tuple[int, str, str]:
        dest = f"{self._user}@{self._host}" if self._user else self._host
        cmd = ["ssh", "-p", str(self._port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
        if self._identity:
            cmd += ["-i", self._identity]
        cmd += [dest, "--", "bash", "-lc", inner]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return p.returncode, p.stdout, p.stderr
        except Exception as e:  # noqa: BLE001
            return 1, "", str(e)

    def run(self) -> None:  # type: ignore[override]
        # Resolve directory and list entries. Print CWD on first line.
        inner = (
            "DIR=\"%s\"; " % shlex.quote(self._path)
            + "if [ -z \"$DIR\" ]; then DIR=\"$HOME\"; fi; "
            + "cd \"$DIR\" 2>/dev/null || { echo '__ERR__ cannot cd'; exit 2; }; pwd; "
            + "ls -1pA | while IFS= read -r n; do "
            + "if [ -d \"$n\" ]; then printf 'D\t%s\n' \"$n\"; "
            + "elif [ -f \"$n\" ]; then printf 'F\t%s\n' \"$n\"; "
            + "else printf 'O\t%s\n' \"$n\"; fi; done"
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
                cmd = f"bash -lc {shlex.quote(inner)}"
                _, stdout, stderr = client.exec_command(cmd, timeout=12)
                out = stdout.read().decode(errors="ignore")
                err = stderr.read().decode(errors="ignore")
                client.close()
                if err and not out:
                    self.error.emit(err.strip())
                    return
            except Exception as e:  # noqa: BLE001
                self.error.emit(str(e))
                return
        else:
            rc, out, err = self._run_remote(inner)
            if rc != 0 and err and not out:
                self.error.emit(err.strip())
                return
        lines = (out or "").splitlines()
        if not lines:
            self.error.emit("empty listing output")
            return
        cwd = lines[0].strip()
        entries = []
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                t, name = ln.split('\t', 1)
            except ValueError:
                t, name = 'O', ln
            entries.append({'type': t, 'name': name})
        self.result.emit(cwd, entries)
