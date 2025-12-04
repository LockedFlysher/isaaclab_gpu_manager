from __future__ import annotations

from typing import Any, Optional, Dict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem, QMessageBox
)

from .ssh_exec import RemoteListDirJob


class RemoteFileDialog(QDialog):
    def __init__(self, host_params: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse Remote Files")
        self.resize(700, 520)
        self._hp = host_params
        self._cwd = ""
        self._selected: Optional[str] = None

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.path_edit = QLineEdit(); self.path_edit.setReadOnly(True)
        self.up_btn = QPushButton("Up")
        self.home_btn = QPushButton("Home")
        top.addWidget(QLabel("Path")); top.addWidget(self.path_edit, 1); top.addWidget(self.up_btn); top.addWidget(self.home_btn)
        v.addLayout(top)

        self.list = QListWidget(); v.addWidget(self.list, 1)
        btns = QHBoxLayout(); btns.addStretch(1)
        self.sel_btn = QPushButton("Select"); self.cancel_btn = QPushButton("Cancel")
        btns.addWidget(self.sel_btn); btns.addWidget(self.cancel_btn)
        v.addLayout(btns)

        self.up_btn.clicked.connect(self._go_up)
        self.home_btn.clicked.connect(lambda: self._list_dir(""))
        self.sel_btn.clicked.connect(self._select_current)
        self.cancel_btn.clicked.connect(self.reject)
        self.list.itemDoubleClicked.connect(self._on_double)

        self._list_dir("")

    def selected_path(self) -> str:
        return self._selected or ""

    def _go_up(self) -> None:
        p = (self._cwd or "/").rstrip("/")
        if not p:
            return
        parent = p.rsplit("/", 1)[0]
        if not parent:
            parent = "/"
        self._list_dir(parent)

    def _on_double(self, item: QListWidgetItem) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        name = item.text()
        if t == 'D':
            path = (self._cwd.rstrip("/") + "/" + name) if self._cwd else name
            self._list_dir(path)
        elif t == 'F':
            # Accept only .py files
            if name.lower().endswith('.py'):
                self._selected = (self._cwd.rstrip("/") + "/" + name) if self._cwd else name
                self.accept()

    def _select_current(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        t = it.data(Qt.ItemDataRole.UserRole)
        name = it.text()
        if t == 'D':
            self._on_double(it)
        else:
            if name.lower().endswith('.py'):
                self._selected = (self._cwd.rstrip("/") + "/" + name) if self._cwd else name
                self.accept()

    def _list_dir(self, path: str) -> None:
        try:
            job = RemoteListDirJob(self._hp["host"], int(self._hp["port"]), self._hp.get("username"), self._hp.get("identity"), self._hp.get("password"), path)
        except Exception:
            return
        def _res(cwd: str, entries: list) -> None:
            self._cwd = cwd
            self.path_edit.setText(cwd)
            self.list.clear()
            # Show dirs first then files
            for t in ('D','F','O'):
                for e in entries:
                    if e.get('type') != t:
                        continue
                    name = str(e.get('name',''))
                    # Only show .py files; always show directories for navigation
                    if t == 'F' and not name.lower().endswith('.py'):
                        continue
                    it = QListWidgetItem(name)
                    it.setData(Qt.ItemDataRole.UserRole, t)
                    self.list.addItem(it)
        def _err(m: str) -> None:
            QMessageBox.warning(self, "Remote browse", m or "list failed")
        job.result.connect(_res)
        job.error.connect(_err)
        job.setParent(self)
        job.start()

