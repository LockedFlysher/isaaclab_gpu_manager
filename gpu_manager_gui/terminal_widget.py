from __future__ import annotations

import math
import re
from typing import Optional

from PyQt6.QtCore import Qt, QEvent, QTimer
from PyQt6.QtGui import QKeyEvent, QTextCursor, QPainter
from PyQt6.QtWidgets import QPlainTextEdit


class TerminalWidget(QPlainTextEdit):
    """Lightweight interactive terminal view.

    Goals: a near-native shell experience good enough for everyday SSH usage
    (readline editing, colors stripped, carriage-return progress bars, line
    clearing). Full TUI apps (vim, top) require a full VT emulator and are
    not in scope here.

    - Read-only from the editor perspective; all keyboard input is forwarded
      to the attached shell via `write()`.
    - Handles a small subset of VT100 controls: CR, LF, BS, ESC[K], ESC[2J],
      and strips color CSI sequences (ESC[...m).
    - Maps common keys to terminal sequences (arrows, Home/End, PgUp/PgDn,
      Delete, Ctrl-C/D/Z/L, etc.). Supports paste.
    - Resizes the remote PTY when the widget resizes.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Display settings
        self.setReadOnly(True)
        try:
            # No wrapping for terminal-like look
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        except Exception:
            pass
        # Keep at most 1000 lines (blocks) to bound memory usage
        try:
            self.setMaximumBlockCount(1000)
        except Exception:
            pass
        self.setUndoRedoEnabled(False)
        self.setCursorWidth(0)  # hide Qt cursor; we draw our own blinking caret
        # Keep a simple last-line buffer and column position to implement CR/EL
        self._line_buf: str = ""
        self._col: int = 0
        # Attached shell (must expose write(), send_line(), and optional resize_pty())
        self._shell = None
        # Pre-compiled regex for minimal ANSI filtering
        self._re_csi_color = re.compile(r"\x1b\[[0-9;]*m")
        self._re_osc = re.compile(r"\x1b\].*?(\x07|\x1b\\)")
        self._re_bracketed = re.compile(r"\x1b\[\?2004[hl]")
        self._pending_scroll_to_end = False
        # Blinking caret state
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._cursor_on = True
        # Ensure focus to capture keys
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # Shell attachment ---------------------------------------------------
    def attach_shell(self, shell) -> None:
        """Attach a running SSHInteractiveShell-like object to send keys.

        The object should implement write(str), send_line(str) and ideally
        resize_pty(cols, rows).
        """
        self._shell = shell
        # Initial resize once connected (do a later event to be safe)
        self._send_resize()

    def detach_shell(self) -> None:
        self._shell = None

    # Output rendering ---------------------------------------------------
    def feed(self, data: str) -> None:
        """Render incoming terminal data with minimal VT handling."""
        if not data:
            return
        # Normalize newlines early, but preserve CR semantics
        data = data.replace("\r\n", "\n")
        i = 0
        while i < len(data):
            ch = data[i]
            i += 1
            if ch == "\x08":  # BS
                if self._col > 0:
                    self._col -= 1
                    try:
                        self._line_buf = self._line_buf[: self._col] + self._line_buf[self._col + 1 :]
                    except Exception:
                        pass
                    self._replace_last_line(self._line_buf)
                continue
            if ch == "\r":  # CR
                self._col = 0
                continue
            if ch == "\n":  # LF
                self._append_newline()
                continue
            if ch == "\x1b":  # ESC ... parse few sequences
                seq, consumed = self._parse_escape(data[i - 1 :])
                if consumed > 0:
                    i += consumed - 1  # already consumed ESC in ch
                    if seq == "EL":  # ESC[K
                        self._line_buf = self._line_buf[: self._col]
                        self._replace_last_line(self._line_buf)
                    elif seq == "ED":  # ESC[2J (clear screen)
                        self._clear_screen()
                    # Other sequences are ignored/stripped
                    continue
                # Unknown escape -> drop it
                continue
            # Printable chunk: extend from this point until control/esc
            start = i - 1
            while i < len(data) and data[i] not in "\x08\r\n\x1b":
                i += 1
            text = data[start:i]
            # Strip common OSC/title and color CSI
            text = self._re_osc.sub("", text)
            text = self._re_bracketed.sub("", text)
            text = self._re_csi_color.sub("", text)
            if not text:
                continue
            # Ensure line is long enough, pad with spaces
            if self._col > len(self._line_buf):
                self._line_buf += " " * (self._col - len(self._line_buf))
            # Overwrite from current col
            new_line = (
                self._line_buf[: self._col] + text + self._line_buf[self._col + len(text) :]
            )
            self._line_buf = new_line
            self._col += len(text)
            self._replace_last_line(self._line_buf)
        # Scroll to end lazily to reduce cursor flicker
        if not self._pending_scroll_to_end:
            self._pending_scroll_to_end = True
            self.viewport().update()

    def paintEvent(self, ev):  # noqa: N802 - Qt override
        super().paintEvent(ev)
        if self._pending_scroll_to_end:
            self._pending_scroll_to_end = False
            cur = self.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
        # Draw a simple blinking caret at (_col) on the last line when focused
        try:
            if self._shell is not None and self.hasFocus() and self._cursor_on:
                doc = self.document()
                blk = doc.lastBlock()
                # Last block length includes a separator; clamp to visible chars
                max_pos = max(0, blk.length() - 1)
                pos = blk.position() + min(max(self._col, 0), max_pos)
                cur2 = QTextCursor(doc)
                cur2.setPosition(pos)
                r = self.cursorRect(cur2)
                p = QPainter(self.viewport())
                try:
                    # Bar-style caret: thin rect using text color with slight transparency
                    w = max(2, int(self.fontMetrics().horizontalAdvance("M") * 0.08))
                    color = self.palette().text().color()
                    color.setAlpha(190)
                    p.fillRect(r.left(), r.top(), w, r.height(), color)
                finally:
                    p.end()
        except Exception:
            pass

    def _append_newline(self) -> None:
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        # Replace last line content before inserting newline
        cur.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cur.insertText(self._line_buf)
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText("\n")
        self.setTextCursor(cur)
        self._line_buf = ""
        self._col = 0

    def _replace_last_line(self, line: str) -> None:
        cur = self.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cur.insertText(line)
        self.setTextCursor(cur)

    def _clear_screen(self) -> None:
        super().clear()
        self._line_buf = ""
        self._col = 0

    def clear(self) -> None:  # type: ignore[override]
        self._clear_screen()

    # Minimal ESC parser: return (token, consumed)
    # Tokens: 'EL' for ESC[K, 'ED' for ESC[2J]; otherwise consumed>0 just strips
    def _parse_escape(self, tail: str) -> tuple[str, int]:
        # tail starts with ESC
        if not tail:
            return "", 0
        # Support ESC[K and ESC[2J and strip OSC/color sequences quickly here when split across chunks
        if tail.startswith("\x1b[K"):
            return "EL", 3
        # ESC[2J or ESC[H or other CSI ... just consume until final byte
        if tail.startswith("\x1b["):
            # Find end of CSI (final byte 0x40..0x7E)
            j = 2
            while j < len(tail):
                b = tail[j]
                if 64 <= ord(b) <= 126:
                    tok = tail[2:j+1]
                    if tok == "2J":
                        return "ED", j + 1
                    # Treat any color/style '...m' as strip-only
                    return "", j + 1
                j += 1
            # Incomplete; consume all to avoid jitter
            return "", len(tail)
        # OSC sequence ESC ] ... BEL or ST; strip it
        if tail.startswith("\x1b]"):
            # Find BEL (\x07) or ST (ESC\\)
            j = 2
            while j < len(tail):
                if tail[j] == "\x07":
                    return "", j + 1
                if tail[j] == "\x1b" and j + 1 < len(tail) and tail[j + 1] == "\\":
                    return "", j + 2
                j += 1
            return "", len(tail)
        # Unknown escapes: consume ESC only
        return "", 1

    # Input handling ------------------------------------------------------
    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if self._shell is None:
            super().keyPressEvent(ev)
            return
        text = ""
        key = ev.key()
        mod = ev.modifiers()

        # Ctrl shortcuts
        if mod & Qt.KeyboardModifier.ControlModifier:
            ctrl_map = {
                Qt.Key.Key_C: "\x03",  # ETX
                Qt.Key.Key_D: "\x04",  # EOT
                Qt.Key.Key_Z: "\x1a",  # SUB
                Qt.Key.Key_L: "\x0c",  # FF (clear)
                Qt.Key.Key_A: "\x01",  # SOH (bol)
                Qt.Key.Key_E: "\x05",  # ENQ (eol)
                Qt.Key.Key_K: "\x0b",  # VT (kill to eol)
                Qt.Key.Key_U: "\x15",  # NAK (kill line)
                Qt.Key.Key_W: "\x17",  # ETB (kill word)
            }
            if key in ctrl_map:
                text = ctrl_map[key]
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                text = "\r"
        else:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                text = "\r"
            elif key == Qt.Key.Key_Backspace:
                text = "\x7f"  # DEL (most shells expect ^?)
            elif key == Qt.Key.Key_Tab:
                text = "\t"
            elif key == Qt.Key.Key_Escape:
                text = "\x1b"
            elif key == Qt.Key.Key_Left:
                text = "\x1b[D"
            elif key == Qt.Key.Key_Right:
                text = "\x1b[C"
            elif key == Qt.Key.Key_Up:
                text = "\x1b[A"
            elif key == Qt.Key.Key_Down:
                text = "\x1b[B"
            elif key == Qt.Key.Key_Home:
                text = "\x1b[H"
            elif key == Qt.Key.Key_End:
                text = "\x1b[F"
            elif key == Qt.Key.Key_PageUp:
                text = "\x1b[5~"
            elif key == Qt.Key.Key_PageDown:
                text = "\x1b[6~"
            elif key == Qt.Key.Key_Delete:
                text = "\x1b[3~"
            elif key in (
                Qt.Key.Key_F1,
                Qt.Key.Key_F2,
                Qt.Key.Key_F3,
                Qt.Key.Key_F4,
                Qt.Key.Key_F5,
                Qt.Key.Key_F6,
                Qt.Key.Key_F7,
                Qt.Key.Key_F8,
                Qt.Key.Key_F9,
                Qt.Key.Key_F10,
                Qt.Key.Key_F11,
                Qt.Key.Key_F12,
            ):
                f_map = {
                    Qt.Key.Key_F1: "OP",
                    Qt.Key.Key_F2: "OQ",
                    Qt.Key.Key_F3: "OR",
                    Qt.Key.Key_F4: "OS",
                    Qt.Key.Key_F5: "[15~",
                    Qt.Key.Key_F6: "[17~",
                    Qt.Key.Key_F7: "[18~",
                    Qt.Key.Key_F8: "[19~",
                    Qt.Key.Key_F9: "[20~",
                    Qt.Key.Key_F10: "[21~",
                    Qt.Key.Key_F11: "[23~",
                    Qt.Key.Key_F12: "[24~",
                }
                suf = f_map.get(key, "")
                if suf.startswith("O"):
                    text = "\x1b" + suf
                else:
                    text = "\x1b" + suf
            else:
                # Regular text (ignore when only modifiers held)
                t = ev.text()
                if t:
                    # Alt+X => ESC x sequence
                    if mod & Qt.KeyboardModifier.AltModifier:
                        text = "\x1b" + t
                    else:
                        text = t

        if text:
            try:
                self._shell.write(text)
            except Exception:
                pass
            # Do not let the editor insert text locally
            return
        # Fallback to default handling
        super().keyPressEvent(ev)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt override
        # Paste: send raw text to remote
        if self._shell is not None:
            try:
                text = source.text()
            except Exception:
                text = None
            if text:
                try:
                    self._shell.write(text)
                except Exception:
                    pass
            return
        super().insertFromMimeData(source)

    # Resize handling -----------------------------------------------------
    def resizeEvent(self, ev) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(ev)
        self._send_resize()

    def _send_resize(self) -> None:
        if not self._shell or not hasattr(self._shell, "resize_pty"):
            return
        try:
            fm = self.fontMetrics()
            # Avoid divide-by-zero
            char_w = max(1, fm.horizontalAdvance("M"))
            char_h = max(1, fm.height())
            cols = max(40, int(self.viewport().width() / char_w))
            rows = max(10, int(self.viewport().height() / char_h))
            self._shell.resize_pty(cols, rows)
        except Exception:
            pass

    # Convenience ---------------------------------------------------------
    def local_echo(self, text: str) -> None:
        """Show a local message in the terminal (prefixed already)."""
        self.feed(text + "\n")

    def send_resize(self) -> None:
        """Public wrapper to trigger a PTY resize based on current widget size."""
        self._send_resize()

    # Caret blink helpers -------------------------------------------------
    def focusInEvent(self, ev: QEvent) -> None:  # noqa: N802 - Qt override
        try:
            self._cursor_on = True
            self._blink_timer.start()
        except Exception:
            pass
        super().focusInEvent(ev)

    def focusOutEvent(self, ev: QEvent) -> None:  # noqa: N802 - Qt override
        try:
            self._blink_timer.stop()
            self._cursor_on = False
            self.viewport().update()
        except Exception:
            pass
        super().focusOutEvent(ev)

    def _toggle_blink(self) -> None:
        try:
            # Only blink when focused; otherwise keep off
            if not self.hasFocus():
                self._cursor_on = False
                return
            self._cursor_on = not self._cursor_on
            self.viewport().update()
        except Exception:
            pass
