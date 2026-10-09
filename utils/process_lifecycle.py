"""Desktop backend lifetime checks, without importing application services."""
import ctypes
from ctypes import wintypes
import os
import sys


def _kernel32():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def join_desktop_job():
    """Join before starting services, including when PyInstaller spawned its worker early."""
    name = os.environ.get('TIPTUNE_WINDOWS_JOB_NAME')
    if sys.platform != 'win32' or not name:
        return
    kernel = _kernel32()
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    job = kernel.OpenJobObjectW(0x0001 | 0x0004, False, name)  # ASSIGN_PROCESS | QUERY
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        process = kernel.GetCurrentProcess()
        assigned = wintypes.BOOL()
        if not kernel.IsProcessInJob(process, job, ctypes.byref(assigned)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not assigned.value and not kernel.AssignProcessToJobObject(job, process):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        # Only the desktop owns a persistent job handle. Keeping this handle open
        # would prevent kill-on-close when the desktop exits during an update.
        kernel.CloseHandle(job)


class ParentProcess:
    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        self.kernel = _kernel32() if sys.platform == 'win32' else None
        if self.kernel:
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not self.handle and ctypes.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: exited PID
                raise ctypes.WinError(ctypes.get_last_error())

    def alive(self):
        if self.kernel:
            if not self.handle:
                return False
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result == 258:  # WAIT_TIMEOUT: process still running
                return True
            if result == 0:  # WAIT_OBJECT_0: process has exited, even if its handle still exists
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
