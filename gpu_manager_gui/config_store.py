from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - dependency provided via requirements
    yaml = None  # type: ignore


CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".isaaclab_gpu_manager")
CONFIG_FILE = os.path.join(CONFIG_DIR, "connections.yaml")


def make_key(host: str, port: int, username: Optional[str]) -> str:
    return f"{username}@{host}:{int(port)}" if username else f"{host}:{int(port)}"


def _default_config() -> Dict[str, Any]:
    return {"version": 1, "last_used_key": None, "profiles": {}}


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
        data.setdefault("profiles", {})
        if not isinstance(data["profiles"], dict):
            data["profiles"] = {}
        return data
    except Exception:
        return _default_config()


def save_config(cfg: Dict[str, Any]) -> None:
    if yaml is None:
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=True, allow_unicode=False)


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

