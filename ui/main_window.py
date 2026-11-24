"""Ana pencere: arama, canli yenileme, servis -> alt surec agaci, yonetim eylemleri."""
from __future__ import annotations

import os
import subprocess
import time
from typing import Optional

from PySide6.QtCore import QEvent, QSettings, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSizePolicy, QStyle, QToolBar, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from core import Snapshot
from core.control import is_admin
from .targets import (can_kill, can_open_or_copy, can_restart, can_start, can_stop,
                      command_line, location_path, resolve_selection)
from .viewmodel import (COLUMNS, DESC_FIRST_COLUMNS, NUMERIC_COLUMNS, Row, build_rows,
                        summary_text)
from .worker import ActionWorker, ScanWorker

ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
INTERVALS = [("Kapalı", 0), ("2 sn", 2000), ("5 sn", 5000), ("10 sn", 10000), ("30 sn", 30000)]
DEFAULT_INTERVAL = 5000
ACTION_TITLES = {"start": "Servisi Başlat", "stop": "Servisi Durdur",
                 "restart": "Servisi Yeniden Başlat", "kill": "Süreci Sonlandır"}
TIMING_LABELS = {"registry": "kayıt defteri", "scm": "servis sorgusu",
                 "list": "süreç listesi", "prime": "CPU ölçümü", "build": "çözümleme"}


def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    return str(v).lower() in ("true", "1", "yes")


class MainWindow(QMainWindow):
    scan_requested = Signal()
    action_requested = Signal(str, object)

    def __init__(self):
        super().__init__()
        self._admin = is_admin()
        self.setWindowTitle("PyServiceMonitor — Python süreçleri ve NSSM servisleri"
                            + ("" if self._admin else "  [Yönetici değil]"))
        self.resize(1250, 700)

        self._settings = QSettings("PyServiceMonitor", "PyServiceMonitor")
        self._snapshot: Optional[Snapshot] = None
        self._busy = False                    # tarama suruyor
        self._action_busy = False             # servis/kill eylemi suruyor
        self._pending_refresh = False
        self._was_minimized = False
        self._sort_col = 0
        self._sort_desc = False
        self._collapsed: set = set()
        self._selected_key: Optional[str] = None

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.refresh)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._rerender)

        self._build_ui()
        self._load_settings()
        self._start_workers()
        self._update_actions()
        QTimer.singleShot(0, self.refresh)    # acilista ilk tarama

    # ------------------------------------------------------------------ UI
    def _mk_action(self, text, icon, tip, slot) -> QAction:
        a = QAction(self.style().standardIcon(icon), text, self)
        a.setToolTip(tip)
        a.triggered.connect(lambda _checked=False: slot())
        return a

    def _build_ui(self):
        SP = QStyle.StandardPixmap
        self.act_start = self._mk_action("Başlat (Start)", SP.SP_MediaPlay,
                                         "Seçili NSSM servisini başlat",
                                         lambda: self._service_action("start"))
        self.act_stop = self._mk_action("Durdur (Stop)", SP.SP_MediaStop,
                                        "Seçili NSSM servisini durdur",
                                        lambda: self._service_action("stop"))
        self.act_restart = self._mk_action("Yeniden Başlat (Restart)", SP.SP_BrowserReload,
                                           "Seçili NSSM servisini yeniden başlat",
                                           lambda: self._service_action("restart"))
        self.act_kill = self._mk_action("Süreci Sonlandır (Kill PID)", SP.SP_DialogCancelButton,
                                        "Seçili servis dışı Python sürecini sonlandır",
                                        self._kill_action)
        self.act_open = self._mk_action("Dosya Konumunu Aç", SP.SP_DirOpenIcon,
                                        "Explorer'da ilgili dosyayı seçili olarak aç",
                                        self._open_location)
        self.act_copy = self._mk_action("Komut Satırını Kopyala", SP.SP_FileIcon,
                                        "Komut satırını panoya kopyala", self._copy_command)

        tb = QToolBar("Eylemler")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for a in (self.act_start, self.act_stop, self.act_restart):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_kill)
        tb.addSeparator()
        tb.addAction(self.act_open)
        tb.addAction(self.act_copy)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ara: servis adı, PID veya dosya yolu…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(340)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        tb.addWidget(self.search)
        self.addToolBar(tb)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.search.setFocus)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        top = QHBoxLayout()
        self.summary = QLabel("Taranıyor…")
        self.summary.setWordWrap(True)
        top.addWidget(self.summary, 1)
        top.addWidget(QLabel("Yenileme:"))
        self.cmb_interval = QComboBox()
        for text, ms in INTERVALS:
            self.cmb_interval.addItem(text, ms)
        self.cmb_interval.currentIndexChanged.connect(lambda _i: self._schedule_next())
        top.addWidget(self.cmb_interval)
        self.chk_pause = QCheckBox("Simge durumunda duraklat")
        self.chk_pause.toggled.connect(lambda _c: self._schedule_next())
        top.addWidget(self.chk_pause)
        self.chk_hidden = QCheckBox("conhost.exe'yi de göster")
        self.chk_hidden.toggled.connect(self._rerender)
        top.addWidget(self.chk_hidden)
        self.btn_refresh = QPushButton("Yenile (F5)")
        self.btn_refresh.setShortcut(QKeySequence("F5"))
        self.btn_refresh.clicked.connect(lambda _c=False: self.refresh())
        top.addWidget(self.btn_refresh)
        root.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(False)    # siralamayi kendimiz yapiyoruz (gruplar sabit kalsin)
        self.tree.itemCollapsed.connect(lambda it: self._collapsed.add(self._key(it)))
        self.tree.itemExpanded.connect(lambda it: self._collapsed.discard(self._key(it)))
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

        header = self.tree.header()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(self._sort_col, Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for c in range(1, len(COLUMNS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(0, 480)
        root.addWidget(self.tree, 1)

        self.lbl_denied = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_denied)
        self.statusBar().showMessage("Hazır")

    @staticmethod
    def _key(item: QTreeWidgetItem) -> str:
        return item.data(0, Qt.ItemDataRole.UserRole)

    # ------------------------------------------------------------ settings
    def _load_settings(self):
        s = self._settings
        try:
            iv = int(s.value("interval_ms", DEFAULT_INTERVAL))
        except (TypeError, ValueError):
            iv = DEFAULT_INTERVAL
        idx = self.cmb_interval.findData(iv)
        self.cmb_interval.setCurrentIndex(idx if idx >= 0 else self.cmb_interval.findData(DEFAULT_INTERVAL))
        self.chk_pause.setChecked(_as_bool(s.value("pause_minimized"), True))
        self.chk_hidden.setChecked(_as_bool(s.value("show_conhost"), False))
        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def _save_settings(self):
        s = self._settings
        s.setValue("interval_ms", self.cmb_interval.currentData())
        s.setValue("pause_minimized", self.chk_pause.isChecked())
        s.setValue("show_conhost", self.chk_hidden.isChecked())
        s.setValue("geometry", self.saveGeometry())

    # ------------------------------------------------------------- workers
    def _start_workers(self):
        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker()
        self._scan_worker.moveToThread(self._scan_thread)
        self.scan_requested.connect(self._scan_worker.scan)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_thread.start()

        self._action_thread = QThread(self)
        self._action_worker = ActionWorker()
        self._action_worker.moveToThread(self._action_thread)
        self.action_requested.connect(self._action_worker.run)
        self._action_worker.finished.connect(self._on_action_finished)
        self._action_thread.start()

    # ------------------------------------------------------ refresh / timer
    def _should_pause(self) -> bool:
        return self.chk_pause.isChecked() and self.isMinimized()

    def refresh(self):
        if self._busy:
            self._pending_refresh = True      # tarama bitince hemen bir daha
            return
        if self._should_pause():
            return
        self._auto_timer.stop()
        self._busy = True
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("Taranıyor…")
        self.scan_requested.emit()

    def _schedule_next(self):
        """Bir sonraki otomatik taramayi planla (tarama bitmeden asla baslatmaz)."""
        self._auto_timer.stop()
        iv = self.cmb_interval.currentData()
        if not iv or self._busy or self._should_pause():
            return
        self._auto_timer.start(int(iv))

    def _scan_done(self):
        self._busy = False
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("Yenile (F5)")
        if self._pending_refresh:
            self._pending_refresh = False
            QTimer.singleShot(0, self.refresh)
        else:
            self._schedule_next()

    def _on_scan_finished(self, snap: Snapshot, elapsed: float):
        self._snapshot = snap
        self._rerender()
        parts = [f"{TIMING_LABELS[k]} {v:.0f}" for k, v in snap.timings.items()
                 if k in TIMING_LABELS and v >= 1]
        msg = (f"Son tarama: {time.strftime('%H:%M:%S', time.localtime(snap.timestamp))} "
               f"— {elapsed * 1000:.0f} ms")
        if parts:
            msg += "   (" + ", ".join(parts) + " ms)"
        self.statusBar().showMessage(msg)
        self.lbl_denied.setText(
            f"⚠ {snap.access_denied_count} sürecin komut satırı okunamadı — yönetici olarak çalıştırın"
            if snap.access_denied_count else "")
        self._scan_done()

    def _on_scan_failed(self, msg: str):
        self.statusBar().showMessage(f"Tarama hatası: {msg}")
        self._scan_done()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            minimized = self.isMinimized()
            if minimized and not self._was_minimized:
                if self.chk_pause.isChecked():
                    self._auto_timer.stop()
            elif not minimized and self._was_minimized:
                if self.cmb_interval.currentData():
                    QTimer.singleShot(0, self.refresh)    # geri donunce tazele
            self._was_minimized = minimized
        super().changeEvent(event)

    # ------------------------------------------------------------- sorting
    def _on_header_clicked(self, col: int):
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = col in DESC_FIRST_COLUMNS
        self.tree.header().setSortIndicator(
            self._sort_col,
            Qt.SortOrder.DescendingOrder if self._sort_desc else Qt.SortOrder.AscendingOrder)
        self._rerender()

    # ----------------------------------------------------------- rendering
    def _rerender(self, *_):
        snap = self._snapshot
        if snap is None:
            return
        query = self.search.text().strip()
        self.summary.setText(summary_text(snap))
        rows = build_rows(snap, self.chk_hidden.isChecked(), self._sort_col,
                          self._sort_desc, query)

        dim_brush = QBrush(self.palette().color(QPalette.ColorGroup.Disabled,
                                                QPalette.ColorRole.Text))
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)          # clear/ekleme sirasinda sahte sinyal olmasin
        try:
            selected = self._selected_key
            vbar = self.tree.verticalScrollBar().value()
            self.tree.clear()
            current = None
            for row in rows:
                top = QTreeWidgetItem(self.tree)
                current = self._fill(top, row, dim_brush, selected) or current
                for child in row.children:
                    ci = QTreeWidgetItem(top)
                    current = self._fill(ci, child, dim_brush, selected) or current
                # arama varken sonuclar gorunsun diye hepsi acik
                top.setExpanded(bool(query) or row.key not in self._collapsed)
            if current is not None:
                self.tree.setCurrentItem(current)
            self.tree.verticalScrollBar().setValue(vbar)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
        self._update_actions()

    def _fill(self, item: QTreeWidgetItem, row: Row, dim_brush: QBrush,
              selected: Optional[str]) -> Optional[QTreeWidgetItem]:
        item.setData(0, Qt.ItemDataRole.UserRole, row.key)
        for c, text in enumerate(row.cells):
            item.setText(c, text)
            item.setToolTip(c, row.tooltip)
            if c in NUMERIC_COLUMNS:
                item.setTextAlignment(c, ALIGN_RIGHT)
            if row.dim:
                item.setForeground(c, dim_brush)
        if row.children:                       # grup/servis satirlarini kalin yap
            f = item.font(0)
            f.setBold(True)
            for c in range(len(row.cells)):
                item.setFont(c, f)
        return item if selected is not None and row.key == selected else None

    # ----------------------------------------------------- secim ve eylemler
    def _selection(self):
        return resolve_selection(self._snapshot, self._selected_key)

    def _on_current_changed(self, cur, _prev):
        self._selected_key = self._key(cur) if cur else None
        self._update_actions()

    def _update_actions(self):
        sel = self._selection()
        idle = not self._action_busy
        self.act_start.setEnabled(idle and can_start(sel))
        self.act_stop.setEnabled(idle and can_stop(sel))
        self.act_restart.setEnabled(idle and can_restart(sel))
        self.act_kill.setEnabled(idle and can_kill(sel))
        self.act_open.setEnabled(can_open_or_copy(sel))
        self.act_copy.setEnabled(can_open_or_copy(sel))

    def _on_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self.tree.setCurrentItem(item)        # secimi/aksiyon durumlarini gunceller
        sel = self._selection()
        if sel.kind not in ("service", "process"):
            return
        menu = QMenu(self)
        if sel.kind == "service":
            for a in (self.act_start, self.act_stop, self.act_restart):
                menu.addAction(a)
        else:
            menu.addAction(self.act_kill)
        menu.addSeparator()
        menu.addAction(self.act_open)
        menu.addAction(self.act_copy)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _confirm(self, title: str, text: str) -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("Evet")
        box.button(QMessageBox.StandardButton.No).setText("Hayır")
        return box.exec() == QMessageBox.StandardButton.Yes

    def _service_action(self, kind: str):
        sel = self._selection()
        allowed = {"start": can_start, "stop": can_stop, "restart": can_restart}[kind]
        if self._action_busy or not allowed(sel):
            return
        svc = sel.service
        n = len(self._snapshot.processes_of(svc.name)) if self._snapshot else 0
        verb = {"start": "başlatılsın", "stop": "durdurulsun", "restart": "yeniden başlatılsın"}[kind]
        text = f"'{svc.name}' servisi {verb} mı?\n\nMevcut durum: {svc.status}"
        if kind in ("stop", "restart"):
            text += (f"\nServis altında çalışan süreç sayısı: {n}"
                     "\n\nServis durdurulunca altındaki betikler de sonlanır.")
        if not self._confirm(ACTION_TITLES[kind], text):
            return
        self._run_action(kind, svc.name, f"'{svc.name}'")

    def _kill_action(self):
        sel = self._selection()
        if self._action_busy or not can_kill(sel):
            return
        p = sel.process
        text = (f"PID {p.pid} sonlandırılsın mı?\n\nHedef: {p.target}\nKullanıcı: {p.username or '?'}"
                "\n\nSüreç ve alt süreçleri zorla kapatılır. Bu işlem geri alınamaz; "
                "kaydedilmemiş veri kaybolabilir.")
        if not self._confirm(ACTION_TITLES["kill"], text):
            return
        self._run_action("kill", {"pid": p.pid, "expected_create_time": p.create_time,
                                  "launcher_pids": list(p.launcher_pids)}, f"PID {p.pid}")

    def _run_action(self, kind: str, arg, label: str):
        self._action_busy = True
        self._update_actions()
        self.statusBar().showMessage(f"{ACTION_TITLES[kind]}: {label} — çalışıyor…")
        self.action_requested.emit(kind, arg)

    def _on_action_finished(self, kind: str, label: str, ok: bool, message: str):
        self._action_busy = False
        self._update_actions()
        if ok:
            self.statusBar().showMessage(f"{ACTION_TITLES[kind]}: {label} — {message}", 15000)
        else:
            self.statusBar().showMessage(f"{ACTION_TITLES[kind]}: {label} — BAŞARISIZ")
            hint = "" if self._admin else "\n\nUygulama yönetici olarak çalışmıyor; bu işlem için gerekebilir."
            QMessageBox.warning(self, ACTION_TITLES[kind], f"{label}\n\n{message}{hint}")
        self.refresh()                         # sonucu hemen ekranda goster

    def _open_location(self):
        sel = self._selection()
        if not can_open_or_copy(sel) or self._snapshot is None:
            return
        path = location_path(sel, self._snapshot)
        if not path:
            QMessageBox.information(self, "Dosya Konumunu Aç",
                                    "Bu satır için bir dosya yolu bulunamadı.")
            return
        path = os.path.normpath(path)
        if not os.path.exists(path):
            QMessageBox.information(self, "Dosya Konumunu Aç", f"Dosya bulunamadı:\n{path}")
            return
        subprocess.Popen(["explorer", "/select,", path], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _copy_command(self):
        cmd = command_line(self._selection())
        if not cmd:
            self.statusBar().showMessage("Bu satır için komut satırı bulunamadı "
                                         "(okuma izni olmayabilir).", 8000)
            return
        QApplication.clipboard().setText(cmd)
        self.statusBar().showMessage("Komut satırı panoya kopyalandı.", 8000)

    # --------------------------------------------------------------- close
    def closeEvent(self, event):
        self._auto_timer.stop()
        self._save_settings()
        for th in (self._scan_thread, self._action_thread):
            th.quit()
        for th in (self._scan_thread, self._action_thread):
            th.wait(5000)
        super().closeEvent(event)
