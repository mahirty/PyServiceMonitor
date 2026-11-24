"""Tarama ve eylemler arka plan thread'lerinde calisir; arayuz donmaz."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal, Slot

from core import Monitor
from core.control import kill_process, restart_service, start_service, stop_service


class ScanWorker(QObject):
    finished = Signal(object, float)     # (Snapshot, sure_saniye)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._monitor = None             # worker thread'inde olusturulur

    @Slot()
    def scan(self):
        try:
            if self._monitor is None:
                self._monitor = Monitor()    # ayni nesne: CPU% ve onbellekler icin sart
            t0 = time.perf_counter()
            snap = self._monitor.snapshot()
            self.finished.emit(snap, time.perf_counter() - t0)
        except Exception as e:           # arayuz hicbir durumda dusmesin
            self.failed.emit(f"{type(e).__name__}: {e}")


class ActionWorker(QObject):
    """Servis start/stop/restart ve kill: ayri thread, taramayi bloklamaz."""
    finished = Signal(str, str, bool, str)   # (eylem, hedef etiketi, basarili, mesaj)

    @Slot(str, object)
    def run(self, kind: str, arg):
        label = f"PID {arg['pid']}" if kind == "kill" else str(arg)
        try:
            if kind == "start":
                res = start_service(arg)
            elif kind == "stop":
                res = stop_service(arg)
            elif kind == "restart":
                res = restart_service(arg)
            elif kind == "kill":
                res = kill_process(**arg)
            else:
                raise ValueError(f"bilinmeyen eylem: {kind}")
            self.finished.emit(kind, label, res.ok, res.message)
        except Exception as e:
            self.finished.emit(kind, label, False, f"{type(e).__name__}: {e}")
