"""Utilities for parsing nvidia-smi and ps outputs.

All parsing keeps types simple (ints for MiB, percents) and returns dicts so
the UI layer can be straightforward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class GpuInfo:
    index: int
    name: str
    uuid: str
    util_percent: int  # 0-100
    mem_total_mib: int
    mem_used_mib: int


@dataclass
class ComputeApp:
    gpu_uuid: str
    pid: int
    process_name: str
    used_memory_mib: int


def parse_gpu_csv(csv_text: str) -> List[GpuInfo]:
    """Parses output of:
    nvidia-smi --query-gpu=index,name,uuid,utilization.gpu,memory.total,memory.used --format=csv,noheader,nounits
    """
    gpus: List[GpuInfo] = []
    for line in csv_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            # Unexpected line; skip
            continue
        try:
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    uuid=parts[2],
                    util_percent=int(parts[3]),
                    mem_total_mib=int(parts[4]),
                    mem_used_mib=int(parts[5]),
                )
            )
        except ValueError:
            # Skip malformed lines
            continue
    return gpus


def parse_compute_apps_csv(csv_text: str) -> List[ComputeApp]:
    """Parses output of:
    nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv,noheader,nounits
    If there are no running processes, csv_text might be empty.
    """
    apps: List[ComputeApp] = []
    for line in csv_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            apps.append(
                ComputeApp(
                    gpu_uuid=parts[0],
                    pid=int(parts[1]),
                    process_name=parts[2],
                    used_memory_mib=int(parts[3]),
                )
            )
        except ValueError:
            continue
    return apps


def parse_ps_pid_user(ps_text: str) -> Dict[int, str]:
    """Parses output of: ps -o pid=,user= -p 123,456
    Returns pid->user mapping.
    """
    mapping: Dict[int, str] = {}
    for line in ps_text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Expect: "123 user"
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        user = parts[-1]
        mapping[pid] = user
    return mapping


def aggregate_user_vram(apps: List[ComputeApp], pid_to_user: Dict[int, str]) -> Dict[str, int]:
    """Sums used_memory_mib per user across all compute apps. Unknown users are
    grouped under 'unknown'.
    """
    totals: Dict[str, int] = {}
    for app in apps:
        user = pid_to_user.get(app.pid, "unknown")
        totals[user] = totals.get(user, 0) + app.used_memory_mib
    return totals


def summarize(gpu_csv: str, apps_csv: str, ps_text: str) -> Tuple[List[GpuInfo], List[ComputeApp], Dict[str, int]]:
    gpus = parse_gpu_csv(gpu_csv)
    apps = parse_compute_apps_csv(apps_csv)
    pid_map = parse_ps_pid_user(ps_text)
    user_totals = aggregate_user_vram(apps, pid_map)
    return gpus, apps, user_totals


def parse_pmon(text: str) -> List[Tuple[int, int, str, int]]:
    """Parses `nvidia-smi pmon -c 1` output.

    Returns a list of tuples (gpu_index, pid, process_name, fb_mib).
    Will ignore rows with pid '-' or fb unknown.
    """
    rows: List[Tuple[int, int, str, int]] = []
    if not text:
        return rows
    # Determine column positions by header line starting with '#'
    header = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#'):
            header = line.lstrip('#').strip().split()
            continue
        parts = line.split()
        if not parts or parts[0] == '#' or parts[1:2] == ['-']:
            continue
        try:
            gpu_idx = int(parts[0])
        except Exception:
            continue
        # Try extract pid
        try:
            pid = int(parts[1])
        except Exception:
            continue

        # Find fb field index if present
        fb_mib = 0
        name = parts[-1] if parts else ""
        if header and 'fb' in header:
            try:
                fb_index = header.index('fb')
                fb_mib = int(parts[fb_index])
            except Exception:
                fb_mib = 0
        else:
            # Fallback: try 'mem' column if numeric and assume MiB
            if header and 'mem' in header:
                try:
                    mi = header.index('mem')
                    fb_mib = int(parts[mi])
                except Exception:
                    fb_mib = 0
        rows.append((gpu_idx, pid, name, max(0, fb_mib)))
    return rows
