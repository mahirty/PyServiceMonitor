"""Servis kontrolu (start/stop/restart) ve surec sonlandirma. GUI'den bagimsiz."""
from __future__ import annotations

import ctypes
import os
import subprocess
from dataclasses import dataclass
from typing import Iterable

import psutil

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _status(name: str) -> str:
    try:
        return psutil.win_service_get(name).status()
    except Exception:
        return "unknown"


def _run(args: list, timeout: int):
    """Konsol penceresi acmadan calistirir (--windowed exe icin stdin=DEVNULL sart)."""
    try:
        cp = subprocess.run(args, capture_output=True, text=True, encoding="oem",
                            errors="replace", timeout=timeout,
                            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, f"Zaman aşımı ({timeout} sn)."
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    text = " ".join(l.strip() for l in (cp.stdout + "\n" + cp.stderr).splitlines() if l.strip())
    return cp.returncode == 0, text


def start_service(name: str) -> ActionResult:
    ok, text = _run(["net", "start", name], 90)
    st = _status(name)
    if ok or st == "running":
        return ActionResult(True, "Servis zaten çalışıyor." if not ok else "Servis başlatıldı.")
    return ActionResult(False, f"{text}\n(Servis durumu: {st})")


def stop_service(name: str) -> ActionResult:
    ok, text = _run(["net", "stop", name], 120)
    st = _status(name)
    if ok or st == "stopped":
        return ActionResult(True, "Servis zaten durmuş." if not ok else "Servis durduruldu.")
    return ActionResult(False, f"{text}\n(Servis durumu: {st})")


def restart_service(name: str) -> ActionResult:
    r = stop_service(name)
    if not r.ok:
        return ActionResult(False, "Durdurulamadı: " + r.message)
    r = start_service(name)
    if not r.ok:
        return ActionResult(False, "Durduruldu ama başlatılamadı: " + r.message)
    return ActionResult(True, "Servis yeniden başlatıldı.")


def kill_process(pid: int, expected_create_time: float = 0.0,
                 launcher_pids: Iterable[int] = (), grace: float = 3.0) -> ActionResult:
    """Sureci, (varsa) venv/py launcher'ini ve tum alt Process'lerini sonlandirir.

    PID yeniden kullanimina karsi: create_time listedeki ile uyusmuyorsa iptal eder.
    """
    launchers = set(launcher_pids)
    try:
        p = psutil.Process(pid)
        if expected_create_time and abs(p.create_time() - expected_create_time) > 1.0:
            return ActionResult(False, "Bu PID artık başka bir sürece ait; işlem iptal edildi.")
        victims = [p] + [a for a in p.parents() if a.pid in launchers]
        targets = {}
        for v in victims:
            targets[v.pid] = v
            for c in v.children(recursive=True):
                targets[c.pid] = c
    except psutil.NoSuchProcess:
        return ActionResult(False, "Süreç zaten sonlanmış.")
    except psutil.AccessDenied:
        return ActionResult(False, "Erişim reddedildi. Uygulamayı yönetici olarak çalıştırın.")

    if os.getpid() in targets:
        return ActionResult(False, "İzleyici kendi sürecini sonlandıramaz.")

    for t in targets.values():
        try:
            t.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _gone, alive = psutil.wait_procs(list(targets.values()), timeout=grace)
    for a in alive:
        try:
            a.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if alive:
        psutil.wait_procs(alive, timeout=2.0)

    left = [pid_ for pid_, t in targets.items() if t.is_running()]
    if left:
        return ActionResult(False, "Sonlandırılamayan PID: " + ", ".join(map(str, left))
                            + "\nYönetici olarak çalıştırmayı deneyin.")
    return ActionResult(True, f"{len(targets)} süreç sonlandırıldı (PID: "
                              + ", ".join(map(str, sorted(targets))) + ").")
