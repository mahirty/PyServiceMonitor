"""NSSM servislerini registry + SCM uzerinden okur (nssm.exe cagirmaz)."""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Tuple

import psutil

from .models import NssmService

SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
CONFIG_TTL = 30.0   # registry taramasi (yavas) bu kadar sn'de bir; durum/PID her seferinde taze

# (name, display, image, application, params, appdir)
_Cfg = Tuple[str, str, str, Optional[str], Optional[str], Optional[str]]
_cache: Dict[str, object] = {"t": 0.0, "cfg": []}


def _reg_value(key, name: str):
    import winreg
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _is_nssm_image(image_path: Optional[str]) -> bool:
    if not image_path:
        return False
    return "nssm" in os.path.basename(str(image_path).strip().strip('"')).lower()


def _scan_registry() -> List[_Cfg]:
    import winreg  # sadece Windows
    found: List[_Cfg] = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SERVICES_KEY) as root:
        count = winreg.QueryInfoKey(root)[0]
        for i in range(count):
            try:
                name = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, name) as sk:
                    image_path = _reg_value(sk, "ImagePath")
                    if not _is_nssm_image(image_path):
                        continue
                    app = params = appdir = None
                    try:
                        with winreg.OpenKey(sk, "Parameters") as pk:
                            app = _reg_value(pk, "Application")
                            params = _reg_value(pk, "AppParameters")
                            appdir = _reg_value(pk, "AppDirectory")
                    except OSError:
                        pass
                    found.append((name, _reg_value(sk, "DisplayName") or name,
                                  str(image_path).strip().strip('"'), app, params, appdir))
            except OSError:
                continue
    return found


def read_nssm_services(timings: Optional[Dict[str, float]] = None) -> List[NssmService]:
    """ImagePath'i nssm*.exe olan tum servisleri dondurur."""
    t = timings if timings is not None else {}

    t0 = time.perf_counter()
    now = time.monotonic()
    if now - float(_cache["t"]) > CONFIG_TTL or not _cache["cfg"]:
        _cache["cfg"] = _scan_registry()
        _cache["t"] = now
    t["registry"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    services = []
    for name, display, image, app, params, appdir in _cache["cfg"]:   # type: ignore[misc]
        status, start_type, pid = "unknown", "unknown", 0
        try:
            d = psutil.win_service_get(name).as_dict()
            status = d.get("status") or status
            start_type = d.get("start_type") or start_type
            pid = d.get("pid") or 0
        except Exception:
            pass
        services.append(NssmService(
            name=name, display_name=display, status=status, start_type=start_type,
            pid=pid, nssm_exe=image, application=app,
            app_parameters=params, app_directory=appdir))
    services.sort(key=lambda s: s.name.lower())
    t["scm"] = (time.perf_counter() - t0) * 1000
    return services
