"""Start Tools: py -3.12 -m tools (--startup-time prints how long the
window took to appear, then quits; --check says what Tools finds on this
computer)."""

from __future__ import annotations

import sys
import time

STARTED = time.perf_counter()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--check" in argv:
        from tools.app import check

        return check.main()
    if sys.platform == "win32":
        try:  # its own taskbar button and icon, not Python's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Tools.ResolveHelper")
        except Exception:  # noqa: BLE001
            pass
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from tools.app.window import MainWindow, apply_theme

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Tools")
    apply_theme(app)
    window = MainWindow(poll="--startup-time" not in argv)
    window.show()
    if "--startup-time" in argv:
        def report():
            print("window shown after {:.0f} ms".format(1000 * (time.perf_counter() - STARTED)))
            app.quit()

        QTimer.singleShot(0, report)
    return app.exec()
