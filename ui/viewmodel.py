"""Snapshot -> ekran satirlari donusumu. Qt'ye bagimli DEGIL (test edilebilir)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from core import NssmService, ProcessInfo, Snapshot

# Listede gosterilmez, ancak servis CPU/RAM toplamlarina KATILIR.
HIDDEN_PROCESS_NAMES = frozenset({"conhost.exe"})

COLUMNS = ["Servis / Hedef", "Tür", "PID", "Durum", "CPU %", "RAM (MB)", "Kullanıcı"]
NUMERIC_COLUMNS = (2, 4, 5)          # sagda hizalanir
DESC_FIRST_COLUMNS = (4, 5)          # ilk tikta buyukten kucuge

STANDALONE_KEY = "grp:standalone"
STANDALONE_TITLE = "Servis dışı Python betikleri"


@dataclass
class Row:
    key: str                          # yenilemeler arasinda secim/acik-kapali durumu icin
    cells: List[str]
    sort_values: list
    tooltip: str = ""
    dim: bool = False
    children: List["Row"] = field(default_factory=list)


def _fmt(v: float) -> str:
    return f"{v:.1f}"


def _is_hidden(p: ProcessInfo) -> bool:
    return p.name.lower() in HIDDEN_PROCESS_NAMES


# --- filtre -----------------------------------------------------------------
def _terms(query: str) -> List[str]:
    return [t for t in (query or "").lower().split() if t]


def _match(hay: str, terms: List[str]) -> bool:
    return all(t in hay for t in terms)


def _service_hay(s: NssmService) -> str:
    return (f"{s.name} {s.display_name} {s.pid} {s.status} {s.configured_command} "
            f"{s.app_directory or ''}").lower()


def _proc_hay(p: ProcessInfo) -> str:
    return (f"{p.pid} {p.target} {p.name} {p.exe_path or ''} {' '.join(p.cmdline)} "
            f"{p.username or ''} {' '.join(map(str, p.launcher_pids))}").lower()


# --- satirlar ---------------------------------------------------------------
def _process_row(p: ProcessInfo) -> Row:
    tip = [f"Hedef: {p.target}", f"PID: {p.pid}   PPID: {p.ppid}"]
    if p.launcher_pids:
        tip.append("Launcher (venv/py) PID: " + ", ".join(map(str, p.launcher_pids))
                   + "  -> CPU/RAM toplama dahil")
    if p.exe_path:
        tip.append(f"Exe: {p.exe_path}")
    if p.cwd:
        tip.append(f"Çalışma dizini: {p.cwd}")
    if p.cmdline:
        tip.append("Komut: " + " ".join(p.cmdline))
    if p.access_denied:
        tip.append("! Komut satırı okunamadı (yönetici olarak çalıştırmayı deneyin)")
    target = p.target + ("  (venv)" if p.launcher_pids else "")
    user = p.username or ""
    return Row(
        key=f"pid:{p.pid}",
        cells=[target, p.kind, str(p.pid), "", _fmt(p.cpu_percent), _fmt(p.ram_mb), user],
        sort_values=[p.target.lower(), p.kind, p.pid, "", p.cpu_percent, p.ram_mb, user.lower()],
        tooltip="\n".join(tip),
    )


def _sorted(rows: List[Row], col: int, descending: bool) -> List[Row]:
    return sorted(rows, key=lambda r: (r.sort_values[col], str(r.sort_values[0])),
                  reverse=descending)


def build_rows(snap: Snapshot, show_hidden: bool = False, sort_col: int = 0,
               descending: bool = False, query: str = "") -> List[Row]:
    terms = _terms(query)
    rows: List[Row] = []

    for s in snap.services:
        procs = snap.processes_of(s.name)
        cpu, ram, n = snap.service_usage(s.name)       # gizlilere bakmaksizin TOPLAM
        visible = [p for p in procs if show_hidden or not _is_hidden(p)]
        hidden_n = len(procs) - len(visible)

        shown = visible
        if terms and not _match(_service_hay(s), terms):
            shown = [p for p in visible if _match(_proc_hay(p), terms)]
            if not shown:
                continue

        tip = [f"Servis: {s.name}", f"Görünen ad: {s.display_name}",
               f"Durum: {s.status}   Başlangıç: {s.start_type}",
               f"NSSM: {s.nssm_exe}", f"Komut: {s.configured_command}"]
        if s.app_directory:
            tip.append(f"Çalışma dizini: {s.app_directory}")
        tip.append(f"Süreç sayısı: {n}"
                   + (f"  ({hidden_n} gizli, toplamlara dahil)" if hidden_n else ""))
        rows.append(Row(
            key=f"svc:{s.name}",
            cells=[s.name, "NSSM", str(s.pid) if s.pid else "-", s.status,
                   _fmt(cpu), _fmt(ram), ""],
            sort_values=[s.name.lower(), "NSSM", s.pid, s.status, cpu, ram, ""],
            tooltip="\n".join(tip),
            dim=not s.is_running,
            children=_sorted([_process_row(p) for p in shown], sort_col, descending),
        ))
    rows = _sorted(rows, sort_col, descending)

    standalone = [p for p in snap.processes if p.service is None and
                  (show_hidden or not _is_hidden(p))]
    if terms:
        standalone = [p for p in standalone if _match(_proc_hay(p), terms)]
    if standalone:
        cpu = sum(p.cpu_percent for p in standalone)
        ram = sum(p.ram_mb for p in standalone)
        rows.append(Row(
            key=STANDALONE_KEY,
            cells=[f"{STANDALONE_TITLE} ({len(standalone)})", "Grup", "", "",
                   _fmt(cpu), _fmt(ram), ""],
            sort_values=[STANDALONE_TITLE, "Grup", 0, "", cpu, ram, ""],
            tooltip="Herhangi bir NSSM servisine bağlı olmayan python süreçleri",
            children=_sorted([_process_row(p) for p in standalone], sort_col, descending),
        ))
    return rows


def summary_text(snap: Snapshot) -> str:
    running = sum(1 for s in snap.services if s.is_running)
    svc_procs = [p for p in snap.processes if p.service is not None]
    alone = [p for p in snap.processes if p.service is None]
    return (f"NSSM servisi: {len(snap.services)} (çalışan {running})   |   "
            f"Servis içi: {len(svc_procs)} süreç, "
            f"CPU {_fmt(sum(p.cpu_percent for p in svc_procs))}%, "
            f"RAM {_fmt(sum(p.ram_mb for p in svc_procs))} MB   |   "
            f"Servis dışı python: {len(alone)} süreç, "
            f"CPU {_fmt(sum(p.cpu_percent for p in alone))}%, "
            f"RAM {_fmt(sum(p.ram_mb for p in alone))} MB")
