"""py -m tools --check: what Tools finds on this computer (the end of
setup_mac.sh, and a first thing to send when something does not work)."""

from __future__ import annotations

import os
import platform
import sys


def report() -> list[str]:
    from tools import __version__
    from tools.app import settings
    from tools.core import media
    from tools.resolve import link, look

    lines = ["Tools {} on {} {} ({}), Python {}".format(__version__, platform.system(), platform.release(),
                                                         platform.machine(), platform.python_version())]
    for name in ("PySide6", "numpy", "av"):
        try:
            module = __import__(name)
            lines.append("  {}: {}".format(name, getattr(module, "__version__", "yes")))
        except ImportError:
            lines.append("  {}: MISSING (pip install -r requirements.txt)".format(name))
    ffmpeg = media.find_ffmpeg()
    lines.append("  FFmpeg program: {}".format(ffmpeg or "not found (fine: PyAV reads every file, long ones "
                                                         "a little slower)"))
    library = next((p for p in link.library_paths() if os.path.isfile(p)), None)
    lines.append("  Resolve's scripting library: {}".format(library or "NOT FOUND (is DaVinci Resolve Studio "
                                                                       "installed?)"))
    lines.append("  Resolve running: {}".format("yes" if link.resolve_running() else "no"))
    lines.append("  LUT folders: {}".format(", ".join(look.lut_folders()) or "none found"))
    lines.append("  Settings: {}".format(settings.data_dir()))
    return lines


def main() -> int:
    print("\n".join(report()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
