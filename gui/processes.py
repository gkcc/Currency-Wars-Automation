"""Terminate only a previously recorded owned child using one native handle."""
import ctypes
from ctypes import wintypes


def stop_identity(pid, identity):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
    if not handle:
        raise RuntimeError('Cannot obtain the owned process handle')
    try:
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, created, exited, system, user):
            raise RuntimeError('Cannot verify the owned process creation')
        actual = 'windows:' + str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        if actual != identity:
            return
        if not kernel.TerminateProcess(handle, 2):
            raise RuntimeError('Cannot stop the owned child')
        if kernel.WaitForSingleObject(handle, 3000) != 0:
            raise RuntimeError('Owned child exit could not be verified')
    finally:
        kernel.CloseHandle(handle)
