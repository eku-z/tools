"""Run every test: py -3.12 tests/run_tests.py [-k word].

Each tests/test_*.py function named test_* is one test. Tests run
offscreen with a scratch data folder; the generated media is kept in
%TEMP%\\tools_test_media between runs (made again when deleted).
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TOOLS_DATA_DIR"] = tempfile.mkdtemp(prefix="tools_test_data_")
os.environ.setdefault("TOOLS_TEST_MEDIA", os.path.join(tempfile.gettempdir(), "tools_test_media"))


def main(argv: list[str]) -> int:
    word = argv[argv.index("-k") + 1].lower() if "-k" in argv else ""
    passed, failed = 0, []
    began = time.perf_counter()
    for path in sorted(HERE.glob("test_*.py")):
        module = importlib.import_module(path.stem)
        for name in sorted(n for n in dir(module) if n.startswith("test_")):
            label = "{}.{}".format(path.stem, name)
            if word and word not in label.lower():
                continue
            t0 = time.perf_counter()
            try:
                getattr(module, name)()
            except Exception:  # noqa: BLE001
                failed.append(label)
                print("FAIL {} ({:.2f} s)\n{}".format(label, time.perf_counter() - t0, traceback.format_exc()))
            else:
                passed += 1
                print("ok   {} ({:.2f} s)".format(label, time.perf_counter() - t0))
    print("\n{} passed, {} failed in {:.1f} s".format(passed, len(failed), time.perf_counter() - began))
    for label in failed:
        print("  failed: " + label)
    return 1 if failed or not passed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
