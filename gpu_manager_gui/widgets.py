from __future__ import annotations

from PyQt6.QtCore import Qt, QEvent, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTabBar, QStackedWidget, QSplitter

from .terminal_widget import TerminalWidget


class TopTabs(QWidget):
    """Left-aligned top tab bar + stacked pages (workaround for centered QTabWidget on macOS)."""
    def __init__(self) -> None:
        super().__init__()
        self._bar = QTabBar(movable=False)
        self._bar.setExpanding(False)  # do not stretch; keep tabs compact
        self._stack = QStackedWidget()
        v = QVBoxLayout(self)
        top = QHBoxLayout(); top.addWidget(self._bar); top.addStretch(1)
        v.addLayout(top)
        v.addWidget(self._stack, 1)
        self._bar.currentChanged.connect(self._stack.setCurrentIndex)

    def addTab(self, w: QWidget, title: str) -> None:
        idx = self._stack.addWidget(w)
        self._bar.addTab(title)
        if self._bar.count() == 1:
            self._bar.setCurrentIndex(0)
            self._stack.setCurrentIndex(0)

    def widget(self) -> QWidget:
        return self


class ConsoleArea(QWidget):
    """Container that supports multiple TerminalWidget panes with split.

    - Keeps track of the currently focused terminal (active pane).
    - Allows splitting the active pane horizontally or vertically.
    """
    def __init__(self) -> None:
        super().__init__()
        self._root: QWidget = TerminalWidget()
        self._active: TerminalWidget = self._root  # type: ignore[assignment]
        self._v = QVBoxLayout(self)
        try:
            self._v.setContentsMargins(0, 0, 0, 0)
            self._v.setSpacing(0)
        except Exception:
            pass
        self._v.addWidget(self._root)
        self._hook_terminal(self._root)  # type: ignore[arg-type]

    # Public API ----------------------------------------------------------
    def active_terminal(self) -> TerminalWidget:
        return self._active

    def focus_active(self) -> None:
        try:
            self._active.setFocus()
        except Exception:
            pass

    def clear_active(self) -> None:
        try:
            self._active.clear()
        except Exception:
            pass

    def split_active(self, orientation: Qt.Orientation) -> TerminalWidget:
        tgt = self._active
        # If root is a plain terminal, replace root with a splitter
        if self._root is tgt:
            sp = QSplitter(orientation)
            # Remove terminal from layout and set new root
            try:
                self._v.removeWidget(self._root)
            except Exception:
                pass
            self._root.setParent(None)
            sp.addWidget(tgt)
            new_term = TerminalWidget()
            sp.addWidget(new_term)
            self._hook_terminal(new_term)
            self._root = sp
            self._v.addWidget(sp)
            # Equalize sizes after layout settles
            self._equalize_later(sp)
            self._active = new_term
            try:
                new_term.setFocus()
            except Exception:
                pass
            return new_term
        # Otherwise, find the direct parent splitter and replace tgt with a nested splitter
        parent, idx = self._find_parent_splitter(self._root, tgt)
        if parent is None or idx < 0:
            # Fallback: cannot find (shouldn't happen); no-op by creating a sibling in a horizontal splitter at root
            sp = QSplitter(orientation)
            try:
                self._v.removeWidget(self._root)
            except Exception:
                pass
            self._root.setParent(None)
            sp.addWidget(tgt)
            new_term = TerminalWidget()
            sp.addWidget(new_term)
            self._hook_terminal(new_term)
            self._root = sp
            self._v.addWidget(sp)
            self._equalize_later(sp)
            self._active = new_term
            try:
                new_term.setFocus()
            except Exception:
                pass
            return new_term
        nested = QSplitter(orientation)
        # Insert nested splitter at index, and detach tgt from parent
        try:
            # Qt >= 5.13 has replaceWidget; try it first
            if hasattr(parent, "replaceWidget"):
                parent.replaceWidget(idx, nested)
            else:
                parent.insertWidget(idx, nested)
                # Old tgt shifted to idx+1; detach it
                try:
                    w = parent.widget(idx + 1)
                    if w is tgt:
                        w.setParent(None)
                except Exception:
                    pass
        except Exception:
            # Fallback: insert and detach by index logic
            parent.insertWidget(idx, nested)
            try:
                w = parent.widget(idx + 1)
                if w is tgt:
                    w.setParent(None)
            except Exception:
                pass
        # Build two panes: original tgt and a new terminal
        nested.addWidget(tgt)
        new_term = TerminalWidget()
        nested.addWidget(new_term)
        self._hook_terminal(new_term)
        self._equalize_later(nested)
        self._active = new_term
        try:
            new_term.setFocus()
        except Exception:
            pass
        return new_term

    # Internals -----------------------------------------------------------
    def _hook_terminal(self, term: TerminalWidget) -> None:
        # Track focus to know the active pane
        try:
            term.installEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj, ev):  # type: ignore[override]
        # Update active terminal when a pane gains focus
        try:
            if isinstance(obj, TerminalWidget) and ev.type() == QEvent.Type.FocusIn:
                self._active = obj
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def _find_parent_splitter(self, node: QWidget, target: QWidget):
        # Return (QSplitter, index) that directly contains target; (None, -1) if not found
        from PyQt6.QtWidgets import QSplitter as _QS
        if isinstance(node, _QS):
            for i in range(node.count()):
                w = node.widget(i)
                if w is target:
                    return node, i
                res_p, res_i = self._find_parent_splitter(w, target)
                if res_p is not None:
                    return res_p, res_i
        return None, -1

    def _equalize_later(self, sp: 'QSplitter') -> None:
        # After the event loop processes layout, set equal sizes and stretch
        try:
            def _do():
                try:
                    n = sp.count()
                    if n <= 0:
                        return
                    # Set equal stretch so future resizes distribute evenly
                    for i in range(n):
                        try:
                            sp.setStretchFactor(i, 1)
                        except Exception:
                            pass
                    # Equal sizes; using equal numbers gives equal ratios
                    sp.setSizes([1] * n)
                except Exception:
                    pass
            QTimer.singleShot(0, _do)
        except Exception:
            pass

    def close_active(self) -> None:
        """Remove the active terminal pane from the layout, collapsing splitters."""
        try:
            tgt = self._active
        except Exception:
            return
        # If root is the only terminal, replace with a fresh one to keep area usable
        if self._root is tgt:
            try:
                self._v.removeWidget(self._root)
            except Exception:
                pass
            try:
                self._root.setParent(None)
            except Exception:
                pass
            new_t = TerminalWidget()
            self._hook_terminal(new_t)
            self._root = new_t
            self._v.addWidget(new_t)
            self._active = new_t
            try:
                new_t.setFocus()
            except Exception:
                pass
            return
        # Find parent splitter of tgt
        parent, idx = self._find_parent_splitter(self._root, tgt)
        if parent is None or idx < 0:
            # Fallback: treat as root removal
            try:
                self._v.removeWidget(self._root)
            except Exception:
                pass
            try:
                self._root.setParent(None)
            except Exception:
                pass
            new_t = TerminalWidget(); self._hook_terminal(new_t)
            self._root = new_t
            self._v.addWidget(new_t)
            self._active = new_t
            return
        # Remove target from parent splitter
        try:
            w = parent.widget(idx)
            if w is not None:
                w.setParent(None)
        except Exception:
            pass
        # If only one child remains, collapse splitter into its child
        try:
            if parent.count() == 1:
                only = parent.widget(0)
                gparent, gidx = self._find_parent_splitter(self._root, parent)
                if gparent is None:
                    # parent is root
                    try:
                        self._v.removeWidget(self._root)
                    except Exception:
                        pass
                    try:
                        self._root.setParent(None)
                    except Exception:
                        pass
                    self._root = only
                    self._v.addWidget(only)
                else:
                    try:
                        gparent.replaceWidget(gidx, only) if hasattr(gparent, 'replaceWidget') else (gparent.insertWidget(gidx, only), parent.setParent(None))
                    except Exception:
                        pass
                # Active terminal becomes the first TerminalWidget we can find
                t = self._pick_any_terminal(self._root)
                if t is not None:
                    self._active = t
            else:
                # If siblings remain, pick first terminal under parent as active
                t = self._pick_any_terminal(parent)
                if t is not None:
                    self._active = t
        except Exception:
            pass

    def _pick_any_terminal(self, node: QWidget) -> TerminalWidget | None:
        try:
            if isinstance(node, TerminalWidget):
                return node
            from PyQt6.QtWidgets import QSplitter as _QS
            if isinstance(node, _QS):
                for i in range(node.count()):
                    w = node.widget(i)
                    t = self._pick_any_terminal(w)
                    if t is not None:
                        return t
        except Exception:
            pass
        return None

