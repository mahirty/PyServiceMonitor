"""Veri modelleri (UI ve cekirdek arasindaki sozlesme)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class NssmService:
    """Registry + SCM'den okunan bir NSSM servisi (Python olmak zorunda degil)."""
    name: str
    display_name: str
    status: str = "unknown"          # running / stopped / paused / ...
    start_type: str = "unknown"      # automatic / manual / disabled
    pid: int = 0                     # nssm.exe'nin PID'i (calismiyorsa 0)
    nssm_exe: str = ""
    application: Optional[str] = None
    app_parameters: Optional[str] = None
    app_directory: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def configured_command(self) -> str:
        return f"{self.application or ''} {self.app_parameters or ''}".strip()


@dataclass
class ProcessInfo:
    """Mantiksal bir surec. venv/py launcher + gercek yorumlayici tek satirdir."""
    pid: int                          # gercek yorumlayici (veya exe) PID'i
    ppid: int                         # grubun disindaki gercek ebeveyn
    name: str
    kind: str = "other"               # "python" | "other"
    exe_path: Optional[str] = None
    cmdline: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    script: Optional[str] = None      # .py yolu veya "-m modul"
    service: Optional[str] = None     # ait oldugu NSSM servis adi
    cpu_percent: float = 0.0          # toplam CPU'ya normalize (0-100)
    ram_mb: float = 0.0               # working set (launcher dahil)
    username: Optional[str] = None
    create_time: float = 0.0
    launcher_pids: List[int] = field(default_factory=list)  # birlestirilen launcher'lar
    access_denied: bool = False       # komut satiri okunamadi

    @property
    def target(self) -> str:
        """Ekranda gosterilecek hedef: script varsa script, yoksa exe adi."""
        return self.script or self.name

    @property
    def all_pids(self) -> Tuple[int, ...]:
        return (self.pid, *self.launcher_pids)


@dataclass
class Snapshot:
    timestamp: float
    cpu_count: int
    services: List[NssmService]
    processes: List[ProcessInfo]
    access_denied_count: int = 0
    timings: Dict[str, float] = field(default_factory=dict)   # asama -> ms

    def processes_of(self, service_name: str) -> List[ProcessInfo]:
        return [p for p in self.processes if p.service == service_name]

    def service_usage(self, service_name: str) -> Tuple[float, float, int]:
        """(toplam CPU %, toplam RAM MB, mantiksal surec sayisi)"""
        procs = self.processes_of(service_name)
        return (sum(p.cpu_percent for p in procs),
                sum(p.ram_mb for p in procs),
                len(procs))
