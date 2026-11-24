"""Secili satir -> neyin uzerinde islem yapilabilecegi. Qt'ye bagimli DEGIL."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from core import NssmService, ProcessInfo, Snapshot


@dataclass
class Selection:
    kind: str = "none"                       # none | service | process | group
    service: Optional[NssmService] = None
    process: Optional[ProcessInfo] = None


def resolve_selection(snap: Optional[Snapshot], key: Optional[str]) -> Selection:
    if snap is None or not key:
        return Selection()
    if key.startswith("svc:"):
        name = key[4:]
        for s in snap.services:
            if s.name == name:
                return Selection("service", service=s)
    elif key.startswith("pid:"):
        try:
            pid = int(key[4:])
        except ValueError:
            return Selection()
        for p in snap.processes:
            if p.pid == pid:
                return Selection("process", process=p)
    elif key.startswith("grp:"):
        return Selection("group")
    return Selection()


# --- hangi eylem aktif? ------------------------------------------------------
def can_start(sel: Selection) -> bool:
    return sel.kind == "service" and sel.service.status == "stopped"


def can_stop(sel: Selection) -> bool:
    return sel.kind == "service" and sel.service.status == "running"


def can_restart(sel: Selection) -> bool:
    return sel.kind == "service" and sel.service.status == "running"


def can_kill(sel: Selection) -> bool:
    """Sadece servis disi python surecleri. Servis altindakiler servis uzerinden yonetilir."""
    return (sel.kind == "process" and sel.process.service is None
            and sel.process.kind == "python")


def can_open_or_copy(sel: Selection) -> bool:
    return sel.kind in ("service", "process")


# --- dosya yolu / komut satiri ----------------------------------------------
_PY_RE = re.compile(r'"([^"]+\.py)"|(\S+\.py)', re.IGNORECASE)


def _real_script(p: ProcessInfo) -> Optional[str]:
    if p.script and not p.script.startswith("-m "):
        return p.script
    return None


def location_path(sel: Selection, snap: Snapshot) -> Optional[str]:
    """Explorer'da secilecek dosya: once .py betigi, yoksa exe."""
    if sel.kind == "process":
        p = sel.process
        return _real_script(p) or p.exe_path
    if sel.kind == "service":
        s = sel.service
        for p in snap.processes_of(s.name):
            if _real_script(p):
                return _real_script(p)
        m = _PY_RE.search(s.app_parameters or "")
        if m:
            path = m.group(1) or m.group(2)
            if not os.path.isabs(path) and s.app_directory:
                path = os.path.join(s.app_directory, path)
            return path
        return s.application
    return None


def command_line(sel: Selection) -> Optional[str]:
    if sel.kind == "process":
        return subprocess.list2cmdline(sel.process.cmdline) if sel.process.cmdline else None
    if sel.kind == "service":
        s = sel.service
        app = s.application or ""
        if app and " " in app and not app.startswith('"'):
            app = f'"{app}"'
        return f"{app} {s.app_parameters or ''}".strip() or None
    return None
