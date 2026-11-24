"""Wait for another process to be gone (a restarted server waits for the one it replaces, so they never share the port or the folder)."""
import os
import time


def wait_for_exit(pid: int, timeout: float = 20.0) -> bool:
    """True when `pid` is gone (or never existed), False if it was still there after `timeout` seconds. Never signals the process."""
    if pid <= 0 or pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, pid)         # SYNCHRONIZE: wait on it, nothing more
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)                                          # on POSIX signal 0 only tests that the process exists
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.1)
    return False
