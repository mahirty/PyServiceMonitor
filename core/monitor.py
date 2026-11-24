"""Servisleri ve surecleri birlestirip tek bir Snapshot uretir."""
from __future__ import annotations

import time

import psutil

from .models import Snapshot
from .nssm import read_nssm_services
from .processes import ProcessScanner


class Monitor:
    """Uzun omurlu nesne: canli yenilemede ayni Monitor'u tekrar kullanin
    (CPU% hesabi ve onbellekler icin gerekli)."""

    def __init__(self, prime_interval: float = 1.0):
        self._scanner = ProcessScanner(prime_interval)

    def snapshot(self) -> Snapshot:
        timings: dict = {}
        services = read_nssm_services(timings)
        processes, denied = self._scanner.scan(services, timings)
        return Snapshot(
            timestamp=time.time(),
            cpu_count=psutil.cpu_count() or 1,
            services=services,
            processes=processes,
            access_denied_count=denied,
            timings=timings,
        )
