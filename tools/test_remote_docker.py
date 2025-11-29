#!/usr/bin/env python3
"""
Remote Docker detection (matches GUI logic) with full debug output.

One‑click defaults (as requested):
  host=10.12.55.99 user=wzx port=22 password=waitforyou1314!

So you can simply run:
  python tools/test_remote_docker.py

Or override any field:
  python tools/test_remote_docker.py --host 10.12.55.99 --user wzx --port 22 --password '***'
  python tools/test_remote_docker.py --identity ~/.ssh/id_rsa

It prints:
  - [docker-detect] whoami / PATH / DOCKER_HOST
  - which docker binary is used
  - docker ps outputs
  - final container list
"""
from __future__ import annotations
import argparse
import shlex
import sys


def detect_with_paramiko(host: str, port: int, user: str, password: str | None, identity: str | None, timeout: float = 12.0) -> int:
    try:
        import paramiko  # type: ignore
    except Exception as e:
        print(f"[local-error] paramiko import failed: {e}")
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            key_filename=identity,
            timeout=timeout,
            banner_timeout=max(timeout, 15.0),
            auth_timeout=max(timeout, 15.0),
            allow_agent=True,
            look_for_keys=True,
        )
    except Exception as e:
        print(f"[local-error] SSH connect failed: {e}")
        return 3

    script = (
        "echo '[docker-detect] start' 1>&2; "
        "echo '[docker-detect] whoami='$(whoami)' shell='$SHELL 1>&2; "
        # source rc files
        "if [ -f $HOME/.bashrc ]; then . $HOME/.bashrc >/dev/null 2>&1; fi; "
        "if [ -f $HOME/.bash_profile ]; then . $HOME/.bash_profile >/dev/null 2>&1; fi; "
        "if [ -f $HOME/.profile ]; then . $HOME/.profile >/dev/null 2>&1; fi; "
        # PATH fallback
        "export PATH=\"$PATH:/usr/bin:/usr/local/bin\"; echo '[docker-detect] PATH='$PATH 1>&2; "
        # rootless fallback
        "if [ -z \"$DOCKER_HOST\" ] && [ -n \"$XDG_RUNTIME_DIR\" ] && [ -S \"$XDG_RUNTIME_DIR/docker.sock\" ]; then export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock; fi; "
        "echo '[docker-detect] DOCKER_HOST='${DOCKER_HOST:-'(default)'} 1>&2; "
        # docker path
        "DOCKERCMD=$(command -v docker 2>/dev/null || true); if [ -z \"$DOCKERCMD\" ] && [ -x /usr/bin/docker ]; then DOCKERCMD=/usr/bin/docker; fi; "
        "OUT1=\"\"; OUT2=\"\"; OUT3=\"\"; OUT4=\"\"; "
        "if [ -n \"$DOCKERCMD\" ]; then echo '[docker-detect] using '$DOCKERCMD 1>&2; OUT1=\"$($DOCKERCMD ps --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; OUT3=\"$($DOCKERCMD ps -a --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; fi; "
        "OUT2=\"$(sudo -n docker ps --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; OUT4=\"$(sudo -n docker ps -a --format '{{.Names}}\t{{.ID}}' 2>/dev/null || true)\"; "
        "printf '%s\\n%s\\n%s\\n%s\\n' \"$OUT1\" \"$OUT2\" \"$OUT3\" \"$OUT4\" | awk 'NF' | sort -u"
    )

    cmd = "bash -lc %s" % shlex.quote(script)
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=max(timeout, 20.0))
        out = stdout.read().decode(errors="ignore")
        err = stderr.read().decode(errors="ignore")
    except Exception as e:
        client.close()
        print(f"[local-error] exec failed: {e}")
        return 4
    finally:
        try:
            client.close()
        except Exception:
            pass

    # print stderr (debug) and stdout (list)
    if err:
        sys.stdout.write(err)
    if out:
        sys.stdout.write(out)
    sys.stdout.flush()

    # Summarize parsed containers
    containers: list[str] = []
    for line in (out or "").splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split("\t")
        name = parts[0].strip() if parts else s
        if name:
            containers.append(name)

    if not containers:
        # Fallback plain names
        try:
            client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, port=port, username=user, password=password, key_filename=identity,
                           timeout=timeout, banner_timeout=max(timeout, 15.0), auth_timeout=max(timeout, 15.0),
                           allow_agent=True, look_for_keys=True)
            cmd2 = "bash -lc %s" % shlex.quote("docker ps --format '{{.Names}}' 2>/dev/null || true")
            stdin2, stdout2, stderr2 = client.exec_command(cmd2, timeout=max(timeout, 15.0))
            out2 = stdout2.read().decode(errors="ignore")
            err2 = stderr2.read().decode(errors="ignore")
            client.close()
            if err2:
                print("[docker-detect] (names) stderr:", err2.strip())
            if out2:
                print("[docker-detect] (names) stdout:\n" + out2, end="")
                for ln in out2.splitlines():
                    ln = ln.strip()
                    if ln:
                        containers.append(ln)
        except Exception as e:
            print("[local-error] fallback exec failed:", e)

    print("[local] containers:", ", ".join(sorted(set(containers))) if containers else "<none>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.12.55.99", help="remote host")
    ap.add_argument("--user", default="wzx", help="remote user")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--password", default="waitforyou1314!", help="password (omit if using identity)")
    ap.add_argument("--identity", default=None, help="private key path")
    ap.add_argument("--timeout", type=float, default=12.0)
    args = ap.parse_args()
    # If both identity and password provided, prefer identity (more robust/non-interactive)
    if args.identity:
        pwd = None
    else:
        pwd = args.password
    print(f"[local] target {args.user}@{args.host}:{args.port} auth={'identity' if args.identity else 'password'}")
    return detect_with_paramiko(args.host, args.port, args.user, pwd, args.identity, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
