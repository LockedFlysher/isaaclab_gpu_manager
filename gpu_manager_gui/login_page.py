from __future__ import annotations

import os
from typing import Any, Dict

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QGroupBox, QVBoxLayout, QComboBox, QLabel, QGridLayout, QLineEdit,
    QSpinBox, QHBoxLayout, QPushButton, QCheckBox, QFileDialog, QMessageBox
)


class LoginPage(QWidget):
    # host, port, username, identity, password, interval
    connect_requested = pyqtSignal(str, int, object, object, object, float)
    test_requested = pyqtSignal(str, int, object, object, object, float)

    def __init__(self) -> None:
        super().__init__()
        box = QGroupBox("SSH Login")
        box.setMinimumWidth(420)
        box.setMaximumWidth(560)
        vbox = QVBoxLayout(box)

        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(False)

        # Single grid with two columns to keep perfect alignment
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # Required (left)
        self.host_edit = QLineEdit(); self.host_edit.setPlaceholderText("server or user@server")
        self.host_edit.setToolTip("Required: hostname or user@hostname")
        self.port_spin = QSpinBox(); self.port_spin.setRange(1, 65535); self.port_spin.setValue(22)
        self.port_spin.setToolTip("Required: SSH port (default 22)")
        self.interval_spin = QSpinBox(); self.interval_spin.setRange(1, 600); self.interval_spin.setValue(5)
        self.interval_spin.setToolTip("Required: polling interval in seconds")

        # Advanced (right)
        self.user_edit = QLineEdit(); self.user_edit.setPlaceholderText("username (optional)")
        self.user_edit.setToolTip("Optional: leave empty to use system ssh defaults or user@ in Host")
        self.ident_edit = QLineEdit(); self.ident_edit.setPlaceholderText("~/.ssh/id_rsa (optional)")
        self.ident_edit.setToolTip("Optional private key path; leave empty to use ssh-agent/keys")
        self.browse_btn = QPushButton("Browse…")
        ident_row = QHBoxLayout(); ident_row.addWidget(self.ident_edit, 1); ident_row.addWidget(self.browse_btn)
        ident_row_w = QWidget(); ident_row_w.setLayout(ident_row)
        self.pass_edit = QLineEdit(); self.pass_edit.setPlaceholderText("password (optional)")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_pass_cb = QCheckBox("Show")
        pass_row = QHBoxLayout(); pass_row.addWidget(self.pass_edit, 1); pass_row.addWidget(self.show_pass_cb)
        pass_row_w = QWidget(); pass_row_w.setLayout(pass_row)
        self.remember_cb = QCheckBox("Remember password (insecure)")
        self.remember_cb.setToolTip("Stores password base64 in YAML; not secure; for convenience only")
        self.auto_connect_cb = QCheckBox("Auto-connect last used on startup")

        # Uniform label widths across both columns
        fm = self.fontMetrics()
        labels = ["Profile", "Host *", "Port *", "Interval *", "User", "Identity", "Password"]
        label_w = max(fm.horizontalAdvance(s) for s in labels) + 8
        def L(text: str):
            lab = QLabel(text); lab.setMinimumWidth(label_w); return lab

        # Row 0: profile spans to the right
        grid.addWidget(L("Profile"), 0, 0)
        grid.addWidget(self.profile_combo, 0, 1, 1, 3)
        # Row 1
        grid.addWidget(L("Host *"), 1, 0); grid.addWidget(self.host_edit, 1, 1)
        grid.addWidget(L("User"), 1, 2); grid.addWidget(self.user_edit, 1, 3)
        # Row 2
        grid.addWidget(L("Port *"), 2, 0); grid.addWidget(self.port_spin, 2, 1)
        grid.addWidget(L("Identity"), 2, 2); grid.addWidget(ident_row_w, 2, 3)
        # Row 3
        grid.addWidget(L("Interval *"), 3, 0); grid.addWidget(self.interval_spin, 3, 1)
        grid.addWidget(L("Password"), 3, 2); grid.addWidget(pass_row_w, 3, 3)
        # Row 4+5 extra switches on the right
        grid.addWidget(L("") , 4, 2); grid.addWidget(self.remember_cb, 4, 3)
        grid.addWidget(L("") , 5, 2); grid.addWidget(self.auto_connect_cb, 5, 3)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        vbox.addLayout(grid)

        # Footer
        self.req_note = QLabel("Fields marked * are required")
        self.req_note.setStyleSheet("color: gray;")
        vbox.addWidget(self.req_note)
        self.connect_btn = QPushButton("Connect")
        self.test_btn = QPushButton("Test")
        # Make buttons exactly the same size (height + min width)
        fm_btn = self.fontMetrics()
        btn_w = max(fm_btn.horizontalAdvance(self.test_btn.text()), fm_btn.horizontalAdvance(self.connect_btn.text())) + 32
        for b in (self.test_btn, self.connect_btn):
            b.setFixedHeight(36)
            b.setMinimumWidth(btn_w)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.test_btn)
        btn_row.addWidget(self.connect_btn)
        vbox.addLayout(btn_row)

        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(box, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

        self.browse_btn.clicked.connect(self._browse_identity)
        self.connect_btn.clicked.connect(self._emit_connect)
        self.profile_combo.currentTextChanged.connect(self._profile_changed)
        self.show_pass_cb.toggled.connect(self._toggle_password_echo)
        self.test_btn.clicked.connect(self._emit_test)

        # Apply basic styles for a cleaner look
        self._apply_styles()

    def _browse_identity(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select SSH identity file", os.path.expanduser("~/.ssh"))
        if path:
            self.ident_edit.setText(path)

    def _emit_connect(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing host", "Please enter host as server or user@server")
            return
        port = int(self.port_spin.value())
        user = self.user_edit.text().strip() or None
        if user is None and "@" in host:
            maybe_user, maybe_host = host.split("@", 1)
            if maybe_user and maybe_host:
                user = maybe_user
                host = maybe_host
        ident = self.ident_edit.text().strip() or None
        password = self.pass_edit.text() or None
        interval = float(self.interval_spin.value())
        self.connect_requested.emit(host, port, user, ident, password, interval)

    def _profile_changed(self, key: str) -> None:
        # MainWindow handles filling; no-op to keep signal
        pass

    def _toggle_password_echo(self, checked: bool) -> None:
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _emit_test(self) -> None:
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Missing host", "Please enter host as server or user@server")
            return
        port = int(self.port_spin.value())
        user = self.user_edit.text().strip() or None
        if user is None and "@" in host:
            maybe_user, maybe_host = host.split("@", 1)
            if maybe_user and maybe_host:
                user = maybe_user
                host = maybe_host
        ident = self.ident_edit.text().strip() or None
        password = self.pass_edit.text() or None
        interval = float(self.interval_spin.value())
        self.test_requested.emit(host, port, user, ident, password, interval)

    def _apply_styles(self) -> None:
        self.connect_btn.setObjectName("primaryButton")
        self.setStyleSheet(
            """
            QGroupBox { font-weight: 600; border: 1px solid #dcdce0; border-radius: 8px; margin-top: 12px; }
            /* Keep title in flow to avoid clipping on macOS */
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; background: transparent; }
            QLineEdit, QSpinBox, QComboBox { min-height: 28px; padding: 4px 6px; border: 1px solid #c9c9ce; border-radius: 6px; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #2d7ef7; }

            /* Button base */
            QPushButton { min-height: 36px; padding: 0 14px; border-radius: 6px; border: 1px solid #2d7ef7; color: #2d7ef7; background: #ffffff; }
            QPushButton:hover { background: #f0f6ff; }
            QPushButton:pressed { background: #dbe9ff; border: 1px solid #1e6de6; color: #1e6de6; }
            QPushButton:disabled { color: #9bb5ec; border: 1px solid #b7cbf5; background: #f5f8ff; }
            QPushButton:checked { background: #eaf2ff; border: 1px solid #2d7ef7; color: #1e6de6; }

            /* Primary buttons */
            QPushButton#primaryButton { background: #2d7ef7; color: white; border: 1px solid #2d7ef7; }
            QPushButton#primaryButton:hover { background: #3a86f8; }
            QPushButton#primaryButton:pressed { background: #1e6de6; border: 1px solid #1e6de6; }
            QPushButton#primaryButton:disabled { background: #9dbcf7; color: white; border: 1px solid #9dbcf7; }

            QLabel { color: #222; }
            """
        )

    # Populate helpers
    def set_profiles(self, keys: list[str]) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for k in keys:
            self.profile_combo.addItem(k)
        self.profile_combo.blockSignals(False)

    def fill_from_profile(self, prof: Dict[str, Any]) -> None:
        self.host_edit.setText(str(prof.get("host", "")))
        self.port_spin.setValue(int(prof.get("port", 22)))
        self.user_edit.setText(str(prof.get("username", "")))
        self.ident_edit.setText(str(prof.get("identity", "")))
        self.interval_spin.setValue(int(float(prof.get("interval", 5))))
        pw = prof.get("password") or ""
        self.pass_edit.setText(pw)
        self.remember_cb.setChecked(bool(prof.get("remember_password", False)))
        # no local toggle; always enable SSH fields
