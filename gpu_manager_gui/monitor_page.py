from __future__ import annotations

import sys
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QAbstractItemView, QHeaderView,
    QProgressBar, QSplitter, QLineEdit, QComboBox, QGridLayout, QFormLayout, QTableWidgetItem, QToolButton, QApplication,
    QCheckBox, QButtonGroup, QSpinBox,
)
from PyQt6.QtCharts import QChart, QChartView, QPieSeries

from .widgets import TopTabs, ConsoleArea


class MonitorPage(QWidget):
    disconnect_requested = pyqtSignal()
    # Signals to bubble actions to MainWindow (works even if parent chain changes)
    docker_refresh_req = pyqtSignal()
    conda_refresh_req = pyqtSignal()
    preview_update_req = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        # Back-reference to MainWindow (set by MainWindow after construction)
        self._mw = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.os_label = QLabel("")
        # Allow selecting/copying the OS text on PyQt6
        try:
            self.os_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        except Exception:
            self.os_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.disconnect_btn = QPushButton("Disconnect")
        top.addWidget(self.os_label)
        top.addStretch(1)
        top.addWidget(self.disconnect_btn)

        center = QWidget()
        hbox = QHBoxLayout(center)
        self.gpu_table = QTableWidget(0, 5)
        self.gpu_table.setHorizontalHeaderLabels(["GPU", "Name", "Util %", "Memory", "Procs"])
        self.gpu_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.gpu_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.gpu_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.gpu_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.gpu_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.gpu_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.gpu_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Top processes table (PID, User, Mem MiB, GPU, Name)
        self.proc_table = QTableWidget(0, 5)
        self.proc_table.setHorizontalHeaderLabels(["PID", "User", "Mem (MiB)", "GPU", "Name"])
        self.proc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.proc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.proc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Left vertical splitter: GPUs (top) and Top Processes (bottom)
        left_split = QSplitter(Qt.Orientation.Vertical)
        left_split.addWidget(self.gpu_table)
        left_split.addWidget(self.proc_table)
        left_split.setStretchFactor(0, 3)
        left_split.setStretchFactor(1, 2)
        self.chart = QChart()
        self.chart.setTitle("Per-user VRAM (MiB)")
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        hbox.addWidget(left_split, 3)
        hbox.addWidget(self.chart_view, 2)
        self.disconnect_btn.clicked.connect(lambda: self.disconnect_requested.emit())

        # Runner panel container (no titled box)
        runner_box = QWidget()
        r_v = QVBoxLayout(runner_box)
        try:
            r_v.setContentsMargins(0, 0, 0, 0)
        except Exception:
            pass

        # Top form: use a grid so labels align nicely
        self.script_edit = QLineEdit(); self.script_edit.setPlaceholderText("/path/to/train.py or play.py")
        self.script_browse = QPushButton("Browse")
        self.conda_combo = QComboBox(); self.conda_combo.setEditable(True); self.conda_combo.setMinimumWidth(220)
        try:
            self.conda_combo.setToolTip("仅主机模式有效：选择远端 conda 环境。")
        except Exception:
            pass
        self.conda_refresh = QPushButton("Refresh")
        top_form = QGridLayout(); top_form.setHorizontalSpacing(12); top_form.setVerticalSpacing(6)
        # Row 0: Presets (moved to the very top as requested)
        self.preset_combo = QComboBox(); self.preset_combo.setEditable(True); self.preset_combo.setMinimumWidth(180)
        self.preset_save = QPushButton("Save")
        self.preset_load = QPushButton("Load")
        self.preset_del = QPushButton("Delete")
        phr = QHBoxLayout(); phr.addWidget(QLabel("Preset")); phr.addWidget(self.preset_combo, 1); phr.addWidget(self.preset_save); phr.addWidget(self.preset_load); phr.addWidget(self.preset_del)
        r_v.addLayout(phr)

        # Row 1: script chooser and refresh
        sf = QHBoxLayout(); sf.addWidget(QLabel("Script")); sf.addWidget(self.script_edit, 1); sf.addWidget(self.script_browse); sf.addSpacing(12); sf.addWidget(self.conda_refresh)
        st_w = QWidget(); st_w.setLayout(sf)
        top_form.addWidget(st_w, 1, 0, 1, 2)
        # Row 2: (reserved)
        spacer_row = QHBoxLayout(); _sp = QWidget(); _sp.setLayout(spacer_row); _sp.setVisible(False)
        top_form.addWidget(_sp, 2, 0, 1, 2)
        # Row 3: mode buttons (conda vs docker) + selectors
        self.mode_conda_btn = QPushButton("Host/Conda"); self.mode_conda_btn.setCheckable(True)
        self.mode_docker_btn = QPushButton("Docker"); self.mode_docker_btn.setCheckable(True)
        # Exclusive selection
        try:
            _grp = QButtonGroup(self); _grp.setExclusive(True); _grp.addButton(self.mode_conda_btn); _grp.addButton(self.mode_docker_btn)
        except Exception:
            pass
        # Visual toggles: default to conda
        self.mode_conda_btn.setChecked(True)
        mode_row = QHBoxLayout(); mode_row.addWidget(self.mode_conda_btn); mode_row.addWidget(self.mode_docker_btn); mode_row.addStretch(1)
        mode_w = QWidget(); mode_w.setLayout(mode_row)
        top_form.addWidget(mode_w, 3, 0)
        # Row 3 right content: Conda / Docker specific widgets
        self.use_docker_cb = QCheckBox("Docker")
        try:
            self.use_docker_cb.setToolTip("切换到容器模式（与 Conda 互斥）。")
        except Exception:
            pass
        self.docker_combo = QComboBox(); self.docker_combo.setEditable(True); self.docker_combo.setMinimumWidth(200)
        try:
            self.docker_combo.setToolTip("运行中的容器（docker ps）。可手动输入容器名或模板（如 isaac-lab-nhb-$(whoami)）。")
        except Exception:
            pass
        self.docker_refresh = QPushButton("Refresh containers")
        crow = QHBoxLayout();
        # Left: conda env selector
        self._lbl_conda = QLabel("Conda")
        crow.addWidget(self._lbl_conda)
        crow.addWidget(self.conda_combo, 1)
        crow.addSpacing(12)
        # Middle: docker container
        crow.addWidget(self.docker_combo, 1)
        crow.addSpacing(12)
        # Right: hint + refresh
        try:
            self._hint_btn = QToolButton()
            self._hint_btn.setText("?")
            self._hint_btn.setToolTip("容器下拉仅显示运行中的容器；也可手动输入容器名，例如 isaac-lab-nhb-$(whoami)。")
            try:
                self._hint_btn.setAutoRaise(True)
            except Exception:
                pass
            try:
                self._hint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            except Exception:
                pass
            try:
                self._hint_btn.setFixedSize(20, 20)
                self._hint_btn.setStyleSheet("QToolButton { border: 1px solid #c9c9ce; border-radius: 10px; min-width: 20px; min-height: 20px; padding: 0; color:#555; }")
            except Exception:
                pass
            crow.addWidget(self._hint_btn)
        except Exception:
            pass
        crow.addWidget(self.conda_refresh)
        crow_w = QWidget(); crow_w.setLayout(crow)
        top_form.addWidget(crow_w, 3, 1)
        top_form.setColumnStretch(1, 1)
        r_v.addLayout(top_form)

        form_row = QHBoxLayout()
        left_form = QFormLayout(); right_form = QFormLayout()
        # Params table (--key=value)
        self.params_table = QTableWidget(0, 2); self.params_table.setHorizontalHeaderLabels(["param", "value"])
        self.params_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.params_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.params_add = QPushButton("+ param"); self.params_del = QPushButton("- param")
        self.params_table.setAlternatingRowColors(True)
        left_form.addRow(self.params_table)
        left_btns = QHBoxLayout(); left_btns.addWidget(self.params_add); left_btns.addWidget(self.params_del); left_btns.addStretch(1)
        left_form.addRow(left_btns)
        # Env table (KEY=VALUE)
        self.env_table = QTableWidget(0, 2); self.env_table.setHorizontalHeaderLabels(["env", "value"])
        self.env_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.env_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.env_add = QPushButton("+ env"); self.env_del = QPushButton("- env")
        self.env_table.setAlternatingRowColors(True)
        right_form.addRow(self.env_table)
        right_btns = QHBoxLayout(); right_btns.addWidget(self.env_add); right_btns.addWidget(self.env_del); right_btns.addStretch(1)
        right_form.addRow(right_btns)
        # Put the two forms into a splitter so user can resize
        lr_split = QSplitter(Qt.Orientation.Horizontal)
        lw = QWidget(); lw.setLayout(left_form)
        rw = QWidget(); rw.setLayout(right_form)
        lr_split.addWidget(lw); lr_split.addWidget(rw)
        lr_split.setStretchFactor(0, 1); lr_split.setStretchFactor(1, 1)
        r_v.addWidget(lr_split, 1)

        # Preview (copy-only here); actual Run moved to Console tab per request
        self.run_btn = QPushButton("Run")  # keep attribute for backward code paths; not added to layout
        try:
            self.run_btn.setObjectName("primaryButton")
        except Exception:
            pass
        ph = QHBoxLayout(); ph.addWidget(QLabel("Preview")); ph.addStretch(1)
        r_v.addLayout(ph)
        self.preview_area = QWidget(); self.preview_vbox = QVBoxLayout(self.preview_area)
        try:
            self.preview_vbox.setContentsMargins(0, 0, 0, 0)
            self.preview_vbox.setSpacing(0)
        except Exception:
            pass
        r_v.addWidget(self.preview_area)

        # Tabs: Monitor vs Runner (top-level main tabs, left-aligned)
        self.main_tabs = TopTabs()
        monitor_tab = QWidget(); mt_l = QVBoxLayout(monitor_tab); mt_l.addLayout(top); mt_l.addWidget(center, 1)
        runner_tab = QWidget(); rt_l = QVBoxLayout(runner_tab)
        rt_l.addWidget(runner_box, 1)
        # Console tab (interactive shell)
        console_tab = QWidget(); ct_l = QVBoxLayout(console_tab)
        # Preview header + per-command list with run/copy in Console tab
        cph = QHBoxLayout();
        cph.addWidget(QLabel("Preview"))
        cph.addSpacing(12)
        self.console_profile_label = QLabel("Preset")
        self.console_profile_combo = QComboBox(); self.console_profile_combo.setEditable(False); self.console_profile_combo.setMinimumWidth(200)
        cph.addWidget(self.console_profile_label)
        cph.addWidget(self.console_profile_combo)
        # Inline GPUs selector to the right of Preset (no extra row)
        try:
            cph.addSpacing(12)
        except Exception:
            pass
        self._gpu_lbl = QLabel("GPUs")
        cph.addWidget(self._gpu_lbl)
        self._gpu_box_wrap = QWidget()
        self._gpu_box_layout = QHBoxLayout(self._gpu_box_wrap)
        try:
            self._gpu_box_layout.setContentsMargins(0, 0, 0, 0)
            self._gpu_box_layout.setSpacing(4)
        except Exception:
            pass
        cph.addWidget(self._gpu_box_wrap, 1)
        self.gpu_sel_all_btn = QToolButton(); self.gpu_sel_all_btn.setText("全选")
        self.gpu_sel_none_btn = QToolButton(); self.gpu_sel_none_btn.setText("清空")
        cph.addWidget(self.gpu_sel_all_btn)
        cph.addWidget(self.gpu_sel_none_btn)
        cph.addStretch(1)
        ct_l.addLayout(cph)
        # Runtime holders
        self._console_gpu_boxes = []  # type: list
        self._console_gpu_count = 0
        # Share model with Runner preset combo and keep selection in sync
        try:
            self.console_profile_combo.setModel(self.preset_combo.model())
            try:
                self.console_profile_combo.setCurrentText(self.preset_combo.currentText())
            except Exception:
                pass
            self.console_profile_combo.currentTextChanged.connect(lambda _=None: self.preset_combo.setCurrentText(self.console_profile_combo.currentText()))
            self.preset_combo.currentTextChanged.connect(lambda _=None: self.console_profile_combo.setCurrentText(self.preset_combo.currentText()))
        except Exception:
            pass
        # Container shell one-liner in console tab
        self.container_shell_row = QWidget()
        csh = QHBoxLayout(self.container_shell_row)
        try:
            csh.setContentsMargins(0, 0, 0, 0)
            csh.setSpacing(0)
        except Exception:
            pass
        self.container_shell_edit = QLineEdit(); self.container_shell_edit.setReadOnly(True)
        self.container_shell_copy = QPushButton("复制")
        self.container_shell_run = QPushButton("运行")
        csh.addWidget(self.container_shell_edit, 1)
        csh.addWidget(self.container_shell_run)
        csh.addWidget(self.container_shell_copy)
        self.console_preview_area = QWidget(); self.console_preview_vbox = QVBoxLayout(self.console_preview_area)
        try:
            self.console_preview_vbox.setContentsMargins(0, 0, 0, 0)
            self.console_preview_vbox.setSpacing(0)
        except Exception:
            pass
        try:
            self.console_preview_vbox.addWidget(self.container_shell_row)
        except Exception:
            pass
        ct_l.addWidget(self.console_preview_area)
        # Console controls
        cons_ctrl = QHBoxLayout()
        self.console_open_btn = QPushButton("Open Host Shell")
        self.console_split_h_btn = QPushButton("Split H")
        self.console_split_v_btn = QPushButton("Split V")
        self.console_close_btn = QPushButton("Close")
        self.console_clear_btn = QPushButton("Clear")
        cons_ctrl.addWidget(self.console_open_btn)
        cons_ctrl.addWidget(self.console_split_h_btn)
        cons_ctrl.addWidget(self.console_split_v_btn)
        cons_ctrl.addWidget(self.console_close_btn)
        cons_ctrl.addWidget(self.console_clear_btn)
        cons_ctrl.addStretch(1)
        ct_l.addLayout(cons_ctrl)
        # Reverse tunnel row (ssh -R)
        rvt = QHBoxLayout()
        rvt.addWidget(QLabel("Reverse Tunnel"))
        self.rvt_user_host = QLineEdit(); self.rvt_user_host.setPlaceholderText("user@remote-host")
        self.rvt_ssh_port = QSpinBox(); self.rvt_ssh_port.setRange(1, 65535); self.rvt_ssh_port.setValue(22)
        self.rvt_bind_port = QSpinBox(); self.rvt_bind_port.setRange(1, 65535); self.rvt_bind_port.setValue(7897)
        self.rvt_local_port = QSpinBox(); self.rvt_local_port.setRange(1, 65535); self.rvt_local_port.setValue(7897)
        self.rvt_start_btn = QPushButton("Start")
        self.rvt_stop_btn = QPushButton("Stop"); self.rvt_stop_btn.setEnabled(False)
        rvt.addWidget(self.rvt_user_host, 1)
        rvt.addWidget(QLabel("ssh port")); rvt.addWidget(self.rvt_ssh_port)
        rvt.addWidget(QLabel("remote")); rvt.addWidget(self.rvt_bind_port)
        rvt.addWidget(QLabel("local")); rvt.addWidget(self.rvt_local_port)
        rvt.addWidget(self.rvt_start_btn)
        rvt.addWidget(self.rvt_stop_btn)
        rvt_w = QWidget(); rvt_w.setLayout(rvt)
        ct_l.addWidget(rvt_w)
        # Console area (pane container)
        self.console_area = ConsoleArea()
        ct_l.addWidget(self.console_area, 1)

        # Assemble tabs
        self.main_tabs.addTab(monitor_tab, "Monitor")
        self.main_tabs.addTab(runner_tab, "Runner")
        self.main_tabs.addTab(console_tab, "Console")

        layout.addWidget(self.main_tabs)

        # Wiring
        self.script_browse.clicked.connect(self._on_browse_script)
        self.use_docker_cb.toggled.connect(self._on_docker_toggle)
        # No target wiring
        # Drive hidden checkbox via visual toggle buttons
        try:
            self.mode_conda_btn.toggled.connect(lambda checked: (self.use_docker_cb.setChecked(False) if checked else None))
            self.mode_docker_btn.toggled.connect(lambda checked: (self.use_docker_cb.setChecked(True) if checked else None))
        except Exception:
            pass
        # Hidden controller is not part of UI
        try:
            self.use_docker_cb.setVisible(False)
        except Exception:
            pass
        self.docker_combo.currentTextChanged.connect(lambda _=None: self.preview_update_req.emit())
        self.conda_combo.currentTextChanged.connect(lambda _=None: self.preview_update_req.emit())
        self.conda_refresh.clicked.connect(self._on_refresh_conda)
        try:
            self.docker_refresh.hide()
        except Exception:
            pass
        try:
            self._apply_runner_mode_visibility()
        except Exception:
            pass
        try:
            # GPU selector events
            self.gpu_sel_all_btn.clicked.connect(lambda: self._console_gpu_select_all(True))
            self.gpu_sel_none_btn.clicked.connect(lambda: self._console_gpu_select_all(False))
            self.console_open_btn.clicked.connect(lambda: getattr(self._mw, '_open_console_shell')() if getattr(self, '_mw', None) and hasattr(self._mw, '_open_console_shell') else None)
            self.container_shell_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.container_shell_edit.text()))
            self.container_shell_run.clicked.connect(lambda: getattr(self._mw, '_run_preview_command')(self.container_shell_edit.text()) if getattr(self, '_mw', None) and hasattr(self._mw, '_run_preview_command') else None)
            self.console_close_btn.clicked.connect(lambda: getattr(self._mw, '_close_console_pane')() if getattr(self, '_mw', None) and hasattr(self._mw, '_close_console_pane') else None)
            self.console_clear_btn.clicked.connect(lambda: self.console_area.clear_active())
            self.console_split_h_btn.clicked.connect(lambda: self.console_area.split_active(Qt.Orientation.Horizontal))
            self.console_split_v_btn.clicked.connect(lambda: self.console_area.split_active(Qt.Orientation.Vertical))
            # Reverse tunnel wiring
            self.rvt_start_btn.clicked.connect(lambda: getattr(self._mw, '_start_reverse_tunnel')() if getattr(self, '_mw', None) and hasattr(self._mw, '_start_reverse_tunnel') else None)
            self.rvt_stop_btn.clicked.connect(lambda: getattr(self._mw, '_stop_reverse_tunnel')() if getattr(self, '_mw', None) and hasattr(self._mw, '_stop_reverse_tunnel') else None)
        except Exception:
            pass

        # Param/env table resize behaviour
        try:
            self.params_table.installEventFilter(self)
            self.env_table.installEventFilter(self)
        except Exception:
            pass

        # Add/del param/env rows
        self.params_add.clicked.connect(lambda: self._add_row(self.params_table))
        self.params_del.clicked.connect(lambda: self._del_selected(self.params_table))
        self.env_add.clicked.connect(lambda: self._add_row(self.env_table))
        self.env_del.clicked.connect(lambda: self._del_selected(self.env_table))

    def set_console_preview_commands(self, cmds: list[str]) -> None:
        """Render per-command preview rows inside Console tab with run+copy controls."""
        try:
            layout = self.console_preview_vbox
        except Exception:
            return
        try:
            keep0 = False
            try:
                keep0 = layout.count() > 0 and layout.itemAt(0).widget() is self.container_shell_row
            except Exception:
                keep0 = False
            start = 1 if keep0 else 0
            for i in range(layout.count() - 1, start - 1, -1):
                it = layout.takeAt(i)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
            layout.setSpacing(0)
        except Exception:
            pass
        try:
            container_cmd = self.container_shell_edit.text().strip()
        except Exception:
            container_cmd = ""
        for cmd in (cmds or []):
            if container_cmd and cmd.strip() == container_cmd:
                continue
            row = QHBoxLayout()
            try:
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(0)
            except Exception:
                pass
            le = QLineEdit(); le.setReadOnly(True); le.setText(cmd)
            btn_run = QPushButton("运行")
            btn_copy = QPushButton("复制")
            try:
                btn_run.clicked.connect(lambda _=False, t=cmd: (hasattr(self, '_mw') and hasattr(self._mw, '_run_preview_command')) and self._mw._run_preview_command(t))
                btn_copy.clicked.connect(lambda _=False, t=cmd: QApplication.clipboard().setText(t))
            except Exception:
                pass
            row.addWidget(le, 1)
            row.addWidget(btn_run)
            row.addWidget(btn_copy)
            w = QWidget(); w.setLayout(row)
            layout.addWidget(w)

    def set_container_shell_command(self, cmd: str) -> None:
        try:
            cmd = cmd or ""
            self.container_shell_edit.setText(cmd)
            visible = bool(cmd.strip())
            try:
                self.container_shell_row.setVisible(visible)
            except Exception:
                pass
        except Exception:
            pass

    def _on_refresh_conda(self) -> None:
        # Delegate to MainWindow to trigger detection
        try:
            use_docker = bool(self.use_docker_cb.isChecked())
        except Exception:
            use_docker = False
        try:
            sys.stdout.write(f"[ui] refresh (from MonitorPage) mode={'docker' if use_docker else 'conda'}\n"); sys.stdout.flush()
        except Exception:
            pass
        if getattr(self, '_mw', None) is not None:
            try:
                if use_docker and hasattr(self._mw, '_detect_remote_docker_containers'):
                    self._mw._detect_remote_docker_containers(True)
                    return
                if (not use_docker) and hasattr(self._mw, '_detect_remote_conda_envs'):
                    self._mw._detect_remote_conda_envs(True)
                    return
            except Exception:
                pass
        if use_docker:
            self.docker_refresh_req.emit()
        else:
            self.conda_refresh_req.emit()

    def _on_browse_script(self) -> None:
        if getattr(self, '_mw', None) is not None and hasattr(self._mw, '_browse_remote_script'):
            self._mw._browse_remote_script()
            return
        p = self.parent()
        if p and hasattr(p, "_browse_remote_script"):
            getattr(p, "_browse_remote_script")()

    def _on_docker_toggle(self, checked: bool) -> None:
        try:
            self.conda_combo.setEnabled(not checked)
            self.conda_refresh.setEnabled(True)
            self.docker_combo.setEnabled(checked)
        except Exception:
            pass
        try:
            if checked:
                try:
                    sys.stdout.write("[ui] docker toggled on\n"); sys.stdout.flush()
                except Exception:
                    pass
                self.docker_refresh_req.emit()
            else:
                try:
                    if self.conda_combo.count() == 0 and getattr(self, '_mw', None) and hasattr(self._mw, '_detect_remote_conda_envs'):
                        self._mw._detect_remote_conda_envs(False)
                    cur = self.conda_combo.currentText().strip()
                    if not cur and self.conda_combo.count() > 0:
                        for pref in ("isaaclab", "isaac", "base"):
                            idx = self.conda_combo.findText(pref)
                            if idx >= 0:
                                self.conda_combo.setCurrentIndex(idx)
                                break
                        else:
                            self.conda_combo.setCurrentIndex(0)
                except Exception:
                    pass
        except Exception:
            pass
        self.preview_update_req.emit()
        try:
            self._apply_runner_mode_visibility()
        except Exception:
            pass

    def _notify_parent_update_preview(self) -> None:
        self.preview_update_req.emit()

    # No target helper needed

    def _apply_column_ratio(self, table: QTableWidget, r_first: float = 1.0/3.0) -> None:
        try:
            total = max(0, table.viewport().width())
            c0 = int(total * max(0.05, min(0.95, r_first)))
            c1 = max(0, total - c0)
            table.setColumnWidth(0, c0)
            table.setColumnWidth(1, c1)
        except Exception:
            pass

    def eventFilter(self, obj, ev):  # type: ignore[override]
        try:
            if ev.type() == QEvent.Type.Resize:
                if obj is self.params_table:
                    self._apply_column_ratio(self.params_table, 1.0/3.0)
                elif obj is self.env_table:
                    self._apply_column_ratio(self.env_table, 1.0/3.0)
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _apply_runner_mode_visibility(self) -> None:
        try:
            d = bool(self.use_docker_cb.isChecked())
        except Exception:
            d = False
        try:
            if hasattr(self, '_lbl_conda'):
                self._lbl_conda.setVisible(not d)
            self.conda_combo.setVisible(not d)
            self.docker_combo.setVisible(d)
            if hasattr(self, '_hint_btn'):
                self._hint_btn.setVisible(d)
            try:
                self.mode_conda_btn.blockSignals(True); self.mode_conda_btn.setChecked(not d); self.mode_conda_btn.blockSignals(False)
                self.mode_docker_btn.blockSignals(True); self.mode_docker_btn.setChecked(d); self.mode_docker_btn.blockSignals(False)
            except Exception:
                pass
        except Exception:
            pass

    def set_preview_commands(self, cmds: list[str]) -> None:
        try:
            layout = self.preview_vbox
        except Exception:
            return
        try:
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
            try:
                layout.setSpacing(0)
            except Exception:
                pass
        except Exception:
            pass
        for cmd in (cmds or []):
            row = QHBoxLayout()
            try:
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(0)
            except Exception:
                pass
            le = QLineEdit(); le.setReadOnly(True); le.setText(cmd)
            btn = QPushButton("复制")
            try:
                btn.clicked.connect(lambda _=False, t=cmd: QApplication.clipboard().setText(t))
            except Exception:
                pass
            row.addWidget(le, 1)
            row.addWidget(btn)
            w = QWidget(); w.setLayout(row)
            layout.addWidget(w)

    def _add_row(self, table: QTableWidget) -> None:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(""))
        table.setItem(row, 1, QTableWidgetItem(""))

    def _del_selected(self, table: QTableWidget) -> None:
        for idx in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(idx)

    def update_snapshot(self, snap: 'Snapshot') -> None:
        rows = len(snap.gpus)
        # Keep GPU selector in Console tab synced with number of GPUs
        try:
            self._ensure_console_gpu_boxes(rows)
        except Exception:
            pass
        self.gpu_table.setRowCount(rows)
        procs_per_uuid = {}
        for app in snap.apps:
            procs_per_uuid[app.gpu_uuid] = procs_per_uuid.get(app.gpu_uuid, 0) + 1
        for r, g in enumerate(snap.gpus):
            idx_item = QTableWidgetItem(str(g.index))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 0, idx_item)
            name_item = QTableWidgetItem(g.name)
            self.gpu_table.setItem(r, 1, name_item)
            util_item = QTableWidgetItem(f"{g.util_percent}%")
            util_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 2, util_item)
            prog = QProgressBar()
            prog.setRange(0, max(1, g.mem_total_mib))
            prog.setValue(g.mem_used_mib)
            prog.setFormat(f"{g.mem_used_mib} / {g.mem_total_mib} MiB")
            self.gpu_table.setCellWidget(r, 3, prog)
            n_procs = procs_per_uuid.get(g.uuid, 0)
            procs_item = QTableWidgetItem(str(n_procs))
            procs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.gpu_table.setItem(r, 4, procs_item)

        # Fill Top Processes table (by VRAM usage), limit to 10
        try:
            # Map GPU uuid -> index for display
            uuid_to_idx = {gi.uuid: gi.index for gi in snap.gpus}
            # Sort apps by used_memory_mib desc
            top_apps = sorted(list(snap.apps), key=lambda a: getattr(a, 'used_memory_mib', 0), reverse=True)[:10]
            self.proc_table.setRowCount(len(top_apps))
            pid_user = getattr(snap, 'pid_user_map', {}) or {}
            for i, app in enumerate(top_apps):
                # PID
                pid_it = QTableWidgetItem(str(app.pid))
                pid_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.proc_table.setItem(i, 0, pid_it)
                # User
                user = pid_user.get(int(app.pid), 'unknown') if isinstance(pid_user, dict) else 'unknown'
                self.proc_table.setItem(i, 1, QTableWidgetItem(str(user)))
                # Mem (MiB)
                mem_it = QTableWidgetItem(str(int(getattr(app, 'used_memory_mib', 0))))
                mem_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.proc_table.setItem(i, 2, mem_it)
                # GPU index
                gidx = uuid_to_idx.get(app.gpu_uuid, '-')
                gidx_it = QTableWidgetItem(str(gidx))
                gidx_it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.proc_table.setItem(i, 3, gidx_it)
                # Name
                self.proc_table.setItem(i, 4, QTableWidgetItem(app.process_name))
        except Exception:
            pass

        base_total = sum(max(0, g.mem_total_mib) for g in snap.gpus)
        used_total = sum(max(0, g.mem_used_mib) for g in snap.gpus)
        user_totals = dict(snap.user_vram_mib)
        used_by_users = sum(max(0, v) for v in user_totals.values())

        system_other = max(0.0, float(used_total) - float(used_by_users))
        free_rest = max(0.0, float(base_total) - float(used_total))

        series = QPieSeries()
        series.setLabelsVisible(True)
        if user_totals:
            for user, mib in sorted(user_totals.items(), key=lambda kv: kv[1], reverse=True):
                val = max(0.01, float(mib))
                series.append(f"{user} ({int(mib)} MiB)", val)
        if system_other > 0.5:
            series.append(f"system/other ({int(system_other)} MiB)", system_other)
        if base_total <= 0:
            series.append("idle", 1)
        elif free_rest > 0.5:
            series.append(f"free ({int(free_rest)} MiB)", free_rest)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle("VRAM Total = users + system + free (MiB)")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self.chart_view.setChart(chart)

    def get_mode(self) -> str:
        return "default"

    def update_console_preset_visibility(self) -> None:
        try:
            vis = self.preset_combo.count() > 0
            self.console_profile_label.setVisible(vis)
            self.console_profile_combo.setVisible(vis)
        except Exception:
            pass

    # --- Console GPU selection helpers ---
    def _ensure_console_gpu_boxes(self, n: int) -> None:
        """Ensure GPU checkbox count matches n; preserve existing selections."""
        try:
            n = int(max(0, n))
        except Exception:
            n = 0
        if n == self._console_gpu_count and self._console_gpu_boxes:
            return
        prev = set(self.get_console_selected_gpus())
        # Clear any existing boxes
        try:
            while self._gpu_box_layout.count():
                it = self._gpu_box_layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
        except Exception:
            pass
        self._console_gpu_boxes = []
        from PyQt6.QtWidgets import QCheckBox as _QCB
        for i in range(n):
            cb = _QCB(str(i))
            try:
                cb.setChecked(i in prev)
            except Exception:
                pass
            try:
                cb.toggled.connect(lambda _=False, _i=i: self.preview_update_req.emit())
            except Exception:
                pass
            self._gpu_box_layout.addWidget(cb)
            self._console_gpu_boxes.append(cb)
        try:
            self._gpu_box_layout.addStretch(1)
        except Exception:
            pass
        self._console_gpu_count = n

    def _console_gpu_select_all(self, state: bool) -> None:
        try:
            for cb in self._console_gpu_boxes:
                cb.blockSignals(True)
                cb.setChecked(bool(state))
                cb.blockSignals(False)
        except Exception:
            pass
        self.preview_update_req.emit()

    def get_console_selected_gpus(self) -> list[int]:
        sel = []
        try:
            for idx, cb in enumerate(self._console_gpu_boxes):
                try:
                    if cb.isChecked():
                        sel.append(idx)
                except Exception:
                    pass
        except Exception:
            return []
        return sel
