from __future__ import annotations

import os
import sys
import threading
import re

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

# Support both `python -m gpu_manager_gui.main` and direct script run
try:
    from .main_window import MainWindow
except Exception:
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from gpu_manager_gui.main_window import MainWindow

"""Entry point for IsaacLab GPU Manager after refactor.

This module keeps only startup code and global styles. UI classes live in
login_page.py, monitor_page.py, widgets.py, remote_file_dialog.py, and main_window.py.
"""

def main() -> None:
    # On macOS, suppress noisy system IMK/TSM logs that clutter the terminal.
    def _install_macos_stderr_filter() -> None:
        if sys.platform != "darwin":
            return
        try:
            r_fd, w_fd = os.pipe()
            orig_err = os.dup(2)
            os.dup2(w_fd, 2)
            os.close(w_fd)
            patterns = [
                "IMKCFRunLoopWakeUpReliable",
                "AdjustCapsLockLEDForKeyTransitionHandling",
                "error messaging the mach port",
            ]
            rx = re.compile("|".join(re.escape(p) for p in patterns))

            def _reader() -> None:
                with os.fdopen(r_fd, "r", errors="ignore", buffering=1) as rf, os.fdopen(orig_err, "w", buffering=1) as out:
                    for line in rf:
                        if rx.search(line):
                            # Drop known macOS IMK/TSM noise lines
                            continue
                        out.write(line)
                        out.flush()

            t = threading.Thread(target=_reader, name="stderr-filter", daemon=True)
            t.start()
        except Exception:
            pass

    _install_macos_stderr_filter()

    app = QApplication(sys.argv)
    # Global styles: unify all buttons and inputs across pages
    try:
        app.setStyleSheet(
            """
            QGroupBox { font-weight: 600; border: 1px solid #dcdce0; border-radius: 8px; margin-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; background: transparent; }
            QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { min-height: 28px; padding: 4px 6px; border: 1px solid #c9c9ce; border-radius: 6px; }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #2d7ef7; }

            /* Buttons (global) */
            QPushButton, QToolButton { min-height: 32px; padding: 0 12px; border-radius: 6px; border: 1px solid #2d7ef7; color: #2d7ef7; background: #ffffff; }
            QPushButton:hover, QToolButton:hover { background: #f0f6ff; }
            QPushButton:pressed, QToolButton:pressed { background: #dbe9ff; border: 1px solid #1e6de6; color: #1e6de6; }
            QPushButton:disabled, QToolButton:disabled { color: #9bb5ec; border: 1px solid #b7cbf5; background: #f5f8ff; }
            QPushButton:checked, QToolButton:checked { background: #eaf2ff; border: 1px solid #2d7ef7; color: #1e6de6; }

            QPushButton#primaryButton, QToolButton#primaryButton { background: #2d7ef7; color: white; border: 1px solid #2d7ef7; }
            QPushButton#primaryButton:hover, QToolButton#primaryButton:hover { background: #3a86f8; }
            QPushButton#primaryButton:pressed, QToolButton#primaryButton:pressed { background: #1e6de6; border: 1px solid #1e6de6; }
            QPushButton#primaryButton:disabled, QToolButton#primaryButton:disabled { background: #9dbcf7; color: white; border: 1px solid #9dbcf7; }

            QLabel { color: #222; }
            """
        )
    except Exception:
        pass
    # App icon (SVG)
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base, "assets", "icon.svg")
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))
    except Exception:
        pass

    w = MainWindow()
    # Also set window icon explicitly (some platforms prefer per-window icon)
    try:
        if os.path.exists(icon_path):
            w.setWindowIcon(QIcon(icon_path))
    except Exception:
        pass

    # macOS Dock icon: use PyObjC if available to set application icon at runtime
    if sys.platform == "darwin":
        try:
            from AppKit import NSImage, NSApplication  # type: ignore
            # Render QIcon -> PNG temp file and feed to NSImage
            from PyQt6.QtGui import QPixmap
            import tempfile
            if os.path.exists(icon_path):
                ico = QIcon(icon_path)
                pm = ico.pixmap(512, 512)
                tmp = tempfile.NamedTemporaryFile(prefix="isaaclab_icon_", suffix=".png", delete=False)
                try:
                    pm.save(tmp.name, "PNG")
                    img = NSImage.alloc().initWithContentsOfFile_(tmp.name)
                    if img is not None:
                        NSApplication.sharedApplication().setApplicationIconImage_(img)
                finally:
                    try:
                        tmp.close()
                    except Exception:
                        pass
        except Exception:
            # PyObjC not available; Dock icon may remain default when not bundled as .app
            pass
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
