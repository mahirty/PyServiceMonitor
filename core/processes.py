"""Surec tarama, servis eslestirme ve venv/py launcher birlestirme."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Tuple

import psutil

from .models import NssmService, ProcessInfo

PY_NAMES = frozenset({"python.exe", "pythonw.exe", "py.exe", "pyw.exe"})
_SAFE_ERRORS = (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError)


def safe(fn, default=None):
    try:
        return fn()
    except _SAFE_ERRORS:
        return default


def extract_script(cmdline: Optional[List[str]], cwd: Optional[str]) -> Optional[str]:
    """cmdline'dan calisan .py yolunu (veya '-m modul') bulur."""
    if not cmdline:
        return None
    args = cmdline[1:]
    for i, a in enumerate(args):
        if a == "-m" and i + 1 < len(args):
            return "-m " + args[i + 1]
        if a == "-c":
            return None
        if a.lower().endswith(".py"):
            path = a.strip('"')
            if not os.path.isabs(path) and cwd:
                path = os.path.normpath(os.path.join(cwd, path))
            return path
    return None


def _same(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a) and bool(b) and os.path.normcase(a) == os.path.normcase(b)


def _is_launcher_pair(parent: ProcessInfo, child: ProcessInfo) -> bool:
    """parent, child'in venv/py launcher'i mi?

    Kosullar: ikisi de python, ayni script (veya -m modul) ve farkli exe.
    Ayni exe + ayni script = kendini yeniden baslatan gercek bir surec; birlestirilmez.
    """
    if parent.kind != "python" or child.kind != "python":
        return False
    if not _same(parent.script, child.script):
        return False
    if _same(parent.exe_path, child.exe_path):
        return False
    return True


def merge_launchers(infos: Dict[int, ProcessInfo]) -> List[ProcessInfo]:
    """Launcher + gercek yorumlayici zincirlerini tek mantiksal surece indirger.

    Sonuc satirinin PID'i en icteki (gercek) yorumlayicidir; CPU/RAM zincirin
    toplamidir; launcher PID'leri launcher_pids'te (ici -> disi) tutulur.
    """
    launcher_child: Dict[int, int] = {}          # launcher_pid -> cocuk_pid
    for child in infos.values():
        parent = infos.get(child.ppid)
        if (parent and parent.pid != child.pid
                and parent.pid not in launcher_child
                and _is_launcher_pair(parent, child)):
            launcher_child[parent.pid] = child.pid

    merged: List[ProcessInfo] = []
    for leaf in infos.values():
        if leaf.pid in launcher_child:           # launcher'in kendisi: satir uretme
            continue
        chain: List[ProcessInfo] = []
        cur = leaf
        while True:
            par = infos.get(cur.ppid)
            if par is not None and launcher_child.get(par.pid) == cur.pid:
                chain.append(par)
                cur = par
            else:
                break
        if chain:
            leaf.launcher_pids = [c.pid for c in chain]
            leaf.cpu_percent += sum(c.cpu_percent for c in chain)
            leaf.ram_mb += sum(c.ram_mb for c in chain)
            leaf.ppid = chain[-1].ppid
            leaf.service = leaf.service or chain[-1].service
        merged.append(leaf)
    return merged


class ProcessScanner:
    """Servis altindaki tum surecleri + servis disi python'lari tarar.

    Performans:
      * CPU% icin yalniz ADAY surecler "isitilir" (tum sistem degil); psutil
        process_iter ayni Process nesnelerini onbellekte tuttugundan sonraki
        taramalar beklemeden anlamli CPU% verir.
      * cmdline/cwd/exe/kullanici surec omru boyunca degismez; (pid, create_time)
        ile onbelleklenir. Her taramada sadece CPU ve bellek okunur.
    """

    def __init__(self, prime_interval: float = 1.0):
        self._prime_interval = prime_interval
        self._first_scan_done = False
        self._ncpu = psutil.cpu_count() or 1
        self._own_pid = os.getpid()
        self._primed: Dict[int, float] = {}      # pid -> create_time
        self._static: Dict[int, tuple] = {}      # pid -> (ct, cmdline, cwd, exe, user)

    # -- yardimcilar ---------------------------------------------------------
    @staticmethod
    def _born_after(parent: psutil.Process, child: psutil.Process) -> bool:
        """PID yeniden kullanimina karsi: cocuk, ebeveynden sonra dogmus olmali."""
        pc, cc = parent.info.get("create_time"), child.info.get("create_time")
        if pc is None or cc is None:
            return True
        return cc >= pc - 1.0

    def _service_descendants(self, services: Iterable[NssmService],
                             procs: Dict[int, psutil.Process]) -> Dict[int, str]:
        children = defaultdict(list)
        for pid, p in procs.items():
            children[p.info.get("ppid")].append(pid)

        pid_service: Dict[int, str] = {}
        for s in services:
            if not s.pid or s.pid not in procs:
                continue
            stack = [s.pid]
            while stack:
                cur = stack.pop()
                for c in children.get(cur, ()):
                    if c == s.pid or c in pid_service:
                        continue
                    if not self._born_after(procs[cur], procs[c]):
                        continue
                    pid_service[c] = s.name
                    stack.append(c)
        return pid_service

    def _static_info(self, p: psutil.Process, ct: float):
        cached = self._static.get(p.pid)
        if cached and cached[0] == ct:
            return cached[1:]
        with p.oneshot():
            cmdline = safe(p.cmdline)
            cwd = safe(p.cwd)
            exe = safe(p.exe)
            user = safe(p.username)
        self._static[p.pid] = (ct, cmdline, cwd, exe, user)
        return cmdline, cwd, exe, user

    def _build(self, p: psutil.Process, service: Optional[str]) -> Optional[ProcessInfo]:
        name = p.info.get("name") or safe(p.name) or "?"
        ct = p.info.get("create_time") or 0.0
        cmdline, cwd, exe, user = self._static_info(p, ct)
        with p.oneshot():
            mem = safe(p.memory_info)
            cpu = safe(p.cpu_percent, 0.0) or 0.0
        if mem is None and not safe(p.is_running, False):
            return None
        kind = "python" if name.lower() in PY_NAMES else "other"
        return ProcessInfo(
            pid=p.pid,
            ppid=p.info.get("ppid") or 0,
            name=name,
            kind=kind,
            exe_path=exe,
            cmdline=cmdline or [],
            cwd=cwd,
            script=extract_script(cmdline, cwd) if kind == "python" else None,
            service=service,
            cpu_percent=cpu / self._ncpu,
            ram_mb=(mem.rss / 1024 / 1024) if mem else 0.0,   # Windows: working set
            username=user,
            create_time=ct,
            access_denied=cmdline is None,
        )

    # -- ana islem -----------------------------------------------------------
    def scan(self, services: List[NssmService],
             timings: Optional[Dict[str, float]] = None) -> Tuple[List[ProcessInfo], int]:
        t = timings if timings is not None else {}

        t0 = time.perf_counter()
        procs: Dict[int, psutil.Process] = {
            p.pid: p for p in psutil.process_iter(["pid", "ppid", "name", "create_time"])
        }
        pid_service = self._service_descendants(services, procs)
        candidates = set(pid_service)
        candidates.update(
            pid for pid, p in procs.items()
            if (p.info.get("name") or "").lower() in PY_NAMES
        )
        for cache in (self._primed, self._static):          # kapanan PID'leri temizle
            for pid in [k for k in cache if k not in procs]:
                del cache[pid]
        t["list"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        newly = 0
        for pid in candidates:
            p = procs[pid]
            ct = p.info.get("create_time") or 0.0
            if self._primed.get(pid) != ct:
                safe(lambda p=p: p.cpu_percent(None))
                self._primed[pid] = ct
                newly += 1
        if newly and not self._first_scan_done:
            time.sleep(self._prime_interval)     # sadece ilk taramada anlamli ilk olcum icin
        self._first_scan_done = True
        t["prime"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        infos: Dict[int, ProcessInfo] = {}
        for pid in candidates:
            try:
                info = self._build(procs[pid], pid_service.get(pid))
            except psutil.NoSuchProcess:
                continue
            if info is not None:
                infos[pid] = info

        merged = merge_launchers(infos)
        # Izleyicinin kendisini (ve varsa launcher'ini) listeden cikar
        merged = [m for m in merged if self._own_pid not in m.all_pids]
        merged.sort(key=lambda m: (m.service is None, (m.service or "").lower(),
                                   m.target.lower(), m.pid))
        denied = sum(1 for m in merged if m.access_denied)
        t["build"] = (time.perf_counter() - t0) * 1000
        return merged, denied
