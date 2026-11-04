"""Connecting to Resolve, ported from ZCuts (app/01_start.py
resolve_library_paths, resolve_running, resolve_has_window,
connect_to_resolve; engine/02_state_and_log.py project_identity;
build 259).

Resolve refuses clips and timelines from one connection in calls made over
another (ZCuts' rule 2), so Tools keeps one link and only opens a new one
when the old one stops answering; ``generation`` counts them, and an Undo
made over an older link is refused.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys

HELP = ("In DaVinci Resolve Studio, open Preferences > System > General, set \"External "
        "scripting using\" to Local and choose \"Allow all\" beside it, then save. If Tools "
        "still cannot connect, restart Resolve. (The free version of Resolve does not let "
        "other apps connect; Studio is needed.)")  # ZCuts RESOLVE_SCRIPTING_HELP, build 206

_dll_folders: list = []


def library_paths() -> list[str]:
    paths = []
    if os.environ.get("RESOLVE_SCRIPT_LIB"):
        paths.append(os.environ["RESOLVE_SCRIPT_LIB"])
    if os.name == "nt":
        roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files")]
        roots += [letter + r":\Program Files" for letter in "CDEFGH"]
        roots.append(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        seen = set()
        for root in roots:
            if root.lower() not in seen:
                seen.add(root.lower())
                paths.append(os.path.join(root, "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll"))
    paths += ["/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
              "/opt/resolve/libs/Fusion/fusionscript.so"]
    return paths


def resolve_running() -> bool:
    """Is Resolve open, with a window on screen? A cheap look at the process
    list: asking the scripting library to connect while Resolve is not there
    can stall the window. Any doubt answers True."""
    try:
        if os.name == "nt":
            return _windows_resolve_running()
        for name in ("Resolve", "resolve"):
            done = subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=3)
            if done.returncode == 0:
                return True
        return False
    except Exception:  # noqa: BLE001 - doubt means try
        return True


def _windows_resolve_running() -> bool:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return True
    found = set()
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == "resolve.exe":
                found.add(entry.th32ProcessID)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return bool(found) and _has_window(found)


def _has_window(process_ids) -> bool:
    """Resolve.exe stays in the process list for a while after its window
    closes (ZCuts build 253); its splash screen counts as open."""
    try:
        import ctypes
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        seen = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def each(window, _):
            if user.IsWindowVisible(window):
                owner = wintypes.DWORD()
                user.GetWindowThreadProcessId(window, ctypes.byref(owner))
                if owner.value in process_ids:
                    seen.append(window)
                    return False
            return True

        callback = callback_type(each)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.EnumWindows(callback, 0)
        return bool(seen)
    except Exception:  # noqa: BLE001
        return True


def connect():
    """(resolve, problem); resolve is None when the connection failed."""
    library = next((path for path in library_paths() if os.path.isfile(path)), None)
    if library is None:
        return None, "DaVinci Resolve's scripting library was not found. Is DaVinci Resolve Studio installed?"
    try:
        if hasattr(os, "add_dll_directory") and not _dll_folders:
            _dll_folders.append(os.add_dll_directory(os.path.dirname(library)))
        module = sys.modules.get("fusionscript")
        if module is None:
            loader = importlib.machinery.ExtensionFileLoader("fusionscript", library)
            spec = importlib.util.spec_from_loader("fusionscript", loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
            sys.modules["fusionscript"] = module
        resolve = module.scriptapp("Resolve")
    except Exception as error:  # noqa: BLE001
        return None, "Resolve's scripting library could not be loaded: {}".format(error)
    if resolve is None:
        return None, "Tools cannot connect to DaVinci Resolve. " + HELP
    return resolve, ""


def project_identity(project):
    """Something that tells two projects apart: GetUniqueId, else the name."""
    if project is None:
        return None
    for getter in ("GetUniqueId", "GetName"):
        try:
            value = getattr(project, getter)()
        except Exception:  # noqa: BLE001
            continue
        if value:
            return "{}:{}".format(getter, value)
    return None


class Link:
    """The one connection to Resolve. ``factory`` makes a new connection
    (``connect`` by default; the tests pass a stand-in Resolve)."""

    def __init__(self, factory=None, running=None):
        self._factory = factory or connect
        self._running = running or (resolve_running if factory is None else (lambda: True))
        self.resolve = None
        self.generation = 0
        self.problem = ""

    def project(self):
        """The open project over the current link, or None (and the link
        dropped) when it no longer answers."""
        if self.resolve is None:
            return None
        try:
            return self.resolve.GetProjectManager().GetCurrentProject()
        except Exception:  # noqa: BLE001 - Resolve closed or restarted
            self.resolve = None
            return None

    def ensure(self):
        """The open project, connecting when there is no working link.
        Returns None with ``problem`` set when Resolve cannot be reached."""
        project = self.project()
        if project is not None:
            self.problem = ""
            return project
        if self.resolve is not None:
            if self._running():
                self.problem = "Resolve is open, but no project is loaded. Open a project in Resolve."
                return None
            self.resolve = None  # Resolve was closed: connect again when it is back
        if not self._running():
            self.problem = "DaVinci Resolve is not open."
            return None
        resolve, problem = self._factory()
        if resolve is None:
            self.problem = problem
            return None
        self.resolve = resolve
        self.generation += 1
        project = self.project()
        self.problem = "" if project is not None else "Resolve is open, but no project is loaded."
        return project
