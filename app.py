"""Uygulama giris noktasi."""
import ctypes
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow

APP_ID = "PyServiceMonitor.App"


def resource_path(rel: str) -> str:
    """Hem normal calismada hem PyInstaller --onefile (_MEIPASS) icinde dogru yol."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def main():
    try:   # Gorev cubugunda "python" yerine kendi ikonumuz/grubumuz gorunsun
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("PyServiceMonitor")
    icon = QIcon(resource_path(os.path.join("assets", "icon.png")))
    if not icon.isNull():
        app.setWindowIcon(icon)          # ana pencere + tum diyaloglar

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
