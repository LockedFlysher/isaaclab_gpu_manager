from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional, List

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - dependency provided via requirements
    yaml = None  # type: ignore


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".isaaclab_gpu_manager")
CONFIG_FILE = os.path.join(CONFIG_DIR, "connections.yaml")


def make_key(host: str, port: int, username: Optional[str]) -> str:
    return f"{username}@{host}:{int(port)}" if username else f"{host}:{int(port)}"


def _default_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "last_used_key": None,
        "auto_connect_last_used": False,
        "profiles": {},
        "runners": {},           # per-host+mode saved state
        "runner_presets": {},     # named, host-agnostic presets
        "last_runner_preset": "", # remember last selected runner preset name
    }


def load_config() -> Dict[str, Any]:
    if yaml is None:
        # minimal in-memory fallback if PyYAML missing
        return _default_config()
    try:
        if not os.path.exists(CONFIG_FILE):
            return _default_config()
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return _default_config()
        data.setdefault("version", 1)
        data.setdefault("last_used_key", None)
        data.setdefault("auto_connect_last_used", False)
        data.setdefault("profiles", {})
        data.setdefault("runners", {})
        data.setdefault("runner_presets", {})
        data.setdefault("last_runner_preset", "")
        if not isinstance(data["profiles"], dict):
            data["profiles"] = {}
        if not isinstance(data["runners"], dict):
            data["runners"] = {}
        if not isinstance(data.get("runner_presets", {}), dict):
            data["runner_presets"] = {}
        return data
    except Exception:
        return _default_config()


def save_config(cfg: Dict[str, Any]) -> None:
    if yaml is None:
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=True, allow_unicode=False)


def yaml_available() -> bool:
    return yaml is not None


def config_path() -> str:
    return CONFIG_FILE


def encode_password(pw: str) -> str:
    return base64.b64encode(pw.encode("utf-8")).decode("ascii")


def decode_password(b64: str) -> str:
    try:
        return base64.b64decode(b64.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def get_profile_password(profile: Dict[str, Any]) -> Optional[str]:
    if not profile or not profile.get("remember_password"):
        return None
    b64 = profile.get("password_b64")
    if not b64:
        return None
    pw = decode_password(str(b64))
    return pw or None


def save_profile(cfg: Dict[str, Any], profile: Dict[str, Any], password: Optional[str]) -> None:
    host = str(profile.get("host", "").strip())
    if not host:
        return
    port = int(profile.get("port", 22))
    username = str(profile.get("username", "").strip()) or None
    identity = str(profile.get("identity", "").strip())
    interval = float(profile.get("interval", 5.0))
    remember = bool(profile.get("remember_password", False))

    key = make_key(host, port, username)
    prof = {
        "host": host,
        "port": port,
        "username": username or "",
        "identity": identity,
        "interval": interval,
        "remember_password": remember,
    }
    if remember and password:
        prof["password_b64"] = encode_password(password)
    else:
        prof.pop("password_b64", None)

    cfg.setdefault("profiles", {})[key] = prof
    cfg["last_used_key"] = key
    save_config(cfg)


def runner_key(host: str, port: int, username: Optional[str]) -> str:
    return make_key(host, port, username)


def load_runner(cfg: Dict[str, Any], key: str, mode: str) -> Dict[str, Any]:
    r = cfg.get("runners", {}).get(key, {}).get(mode, {})
    if not isinstance(r, dict):
        r = {}
    # Normalize structure
    r.setdefault("conda_env", "")
    r.setdefault("script", "")
    r.setdefault("params", [])  # list of [key, value]
    r.setdefault("env", [])     # list of [key, value]
    r.setdefault("use_docker", False)
    r.setdefault("docker_container", "")
    r.setdefault("use_compose", False)
    r.setdefault("compose_dir", "")
    r.setdefault("compose_service", "")
    # Backward/robust compatibility: accept dicts or mixed forms for params/env
    def _to_kv_list(x: Any) -> List[List[str]]:
        res: List[List[str]] = []
        if isinstance(x, dict):
            for k, v in x.items():
                res.append([str(k), "" if v is None else str(v)])
            return res
        if isinstance(x, list):
            for it in x:
                if isinstance(it, (list, tuple)):
                    if not it:
                        continue
                    k = it[0]
                    v = it[1] if len(it) > 1 else ""
                    res.append([str(k), "" if v is None else str(v)])
                elif isinstance(it, dict):
                    for k, v in it.items():
                        res.append([str(k), "" if v is None else str(v)])
                elif isinstance(it, str) and "=" in it:
                    k, v = it.split("=", 1)
                    res.append([k.strip(), v])
        return res
    r["params"] = _to_kv_list(r.get("params", []))
    r["env"] = _to_kv_list(r.get("env", []))
    return r


def save_runner(cfg: Dict[str, Any], key: str, mode: str, runner: Dict[str, Any]) -> None:
    cfg.setdefault("runners", {}).setdefault(key, {})[mode] = {
        "conda_env": runner.get("conda_env", ""),
        "script": runner.get("script", ""),
        "params": runner.get("params", []),
        "env": runner.get("env", []),
        "use_docker": bool(runner.get("use_docker", False)),
        "docker_container": runner.get("docker_container", ""),
        "use_compose": bool(runner.get("use_compose", False)),
        "compose_dir": runner.get("compose_dir", ""),
        "compose_service": runner.get("compose_service", ""),
    }
    save_config(cfg)


# Runner presets (host-agnostic) -------------------------------------------
def list_runner_presets(cfg: Dict[str, Any]) -> list[str]:
    pres = cfg.get("runner_presets", {})
    if not isinstance(pres, dict):
        return []
    return sorted(pres.keys())


def load_runner_preset(cfg: Dict[str, Any], name: str) -> Dict[str, Any]:
    p = cfg.get("runner_presets", {}).get(name) or {}
    if not isinstance(p, dict):
        p = {}
    p.setdefault("conda_env", "")
    p.setdefault("script", "")
    p.setdefault("params", [])
    p.setdefault("env", [])
    p.setdefault("use_docker", False)
    p.setdefault("docker_container", "")
    p.setdefault("use_compose", False)
    p.setdefault("compose_dir", "")
    p.setdefault("compose_service", "")
    # Normalize legacy/mixed formats for params/env
    def _to_kv_list(x: Any) -> List[List[str]]:
        res: List[List[str]] = []
        if isinstance(x, dict):
            for k, v in x.items():
                res.append([str(k), "" if v is None else str(v)])
            return res
        if isinstance(x, list):
            for it in x:
                if isinstance(it, (list, tuple)):
                    if not it:
                        continue
                    k = it[0]
                    v = it[1] if len(it) > 1 else ""
                    res.append([str(k), "" if v is None else str(v)])
                elif isinstance(it, dict):
                    for k, v in it.items():
                        res.append([str(k), "" if v is None else str(v)])
                elif isinstance(it, str) and "=" in it:
                    k, v = it.split("=", 1)
                    res.append([k.strip(), v])
        return res
    p["params"] = _to_kv_list(p.get("params", []))
    p["env"] = _to_kv_list(p.get("env", []))
    return p


def save_runner_preset(cfg: Dict[str, Any], name: str, runner: Dict[str, Any]) -> None:
    if not name:
        return
    cfg.setdefault("runner_presets", {})[name] = {
        "conda_env": runner.get("conda_env", ""),
        "script": runner.get("script", ""),
        "params": runner.get("params", []),
        "env": runner.get("env", []),
        "use_docker": bool(runner.get("use_docker", False)),
        "docker_container": runner.get("docker_container", ""),
        "use_compose": bool(runner.get("use_compose", False)),
        "compose_dir": runner.get("compose_dir", ""),
        "compose_service": runner.get("compose_service", ""),
    }
    save_config(cfg)


def delete_runner_preset(cfg: Dict[str, Any], name: str) -> None:
    try:
        if name in cfg.get("runner_presets", {}):
            del cfg["runner_presets"][name]
            save_config(cfg)
    except Exception:
        pass
