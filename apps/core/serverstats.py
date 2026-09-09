"""Best-effort server resource snapshot — CPU load, memory, disk. Stdlib only
(Linux ``/proc`` + cgroup v2), every reader wrapped so a missing file just
means that metric comes back ``None``. Superadmin-only surface."""

import os
import shutil
from pathlib import Path

_CG = Path("/sys/fs/cgroup")


def _cpu():
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        return None
    cores = os.cpu_count() or 1
    try:
        quota, period = _CG.joinpath("cpu.max").read_text().split()
        if quota != "max":
            cores = max(1, round(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    return {
        "load1": round(load1, 2), "load5": round(load5, 2), "load15": round(load15, 2),
        "cores": cores, "pct": min(100, round(load1 / cores * 100)),
    }


def _memory():
    total = avail = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, val = line.partition(":")
            if key == "MemTotal":
                total = int(val.split()[0]) * 1024
            elif key == "MemAvailable":
                avail = int(val.split()[0]) * 1024
    except (OSError, ValueError):
        pass

    # A cgroup limit (container) beats the host figure.
    try:
        cmax = _CG.joinpath("memory.max").read_text().strip()
        ccur = int(_CG.joinpath("memory.current").read_text().strip())
        if cmax != "max":
            cmax = int(cmax)
            if total is None or cmax < total:
                return {"total": cmax, "used": ccur, "available": cmax - ccur,
                        "pct": round(ccur / cmax * 100) if cmax else 0}
    except (OSError, ValueError):
        pass

    if total and avail is not None:
        used = total - avail
        return {"total": total, "used": used, "available": avail,
                "pct": round(used / total * 100)}
    return None


def _disk(path):
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": u.total, "used": u.used, "free": u.free,
            "pct": round(u.used / u.total * 100) if u.total else 0}


def server_stats():
    from django.conf import settings

    media = str(getattr(settings, "MEDIA_ROOT", "") or "")
    return {
        "cpu": _cpu(),
        "memory": _memory(),
        "disk_root": _disk("/"),
        "disk_media": _disk(media) if media and os.path.isdir(media) else None,
    }
