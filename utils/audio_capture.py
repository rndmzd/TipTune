"""Identify the desktop audio host without confusing it with the Python sidecar."""
import ctypes
from ctypes import wintypes
import ntpath
import os
import sys

from utils.process_lifecycle import _kernel32


def process_executable(pid: int) -> str | None:
    if sys.platform != 'win32' or pid <= 0:
        return None
    kernel = _kernel32()
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        path = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(path))
        if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return ntpath.basename(path.value)
    finally:
        kernel.CloseHandle(handle)
    return None


def desktop_audio_executable() -> str | None:
    """Tauri supplies its own PID when launching the backend, in dev and releases."""
    try:
        pid = int(os.environ.get('TIPTUNE_PARENT_PID', ''))
    except (TypeError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    try:
        return process_executable(pid)
    except OSError:
        return None
