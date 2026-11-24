"""No console windows from the server's own commands.

On Windows a program that has no console (the dashboard started with `--background`) makes every `git` or `claude` it runs open and
close a console window. `install()` makes every subprocess the server starts run without one, unless the caller chose its own flags
(a deliberate terminal for «Resume», a detached start): those are left exactly as they were."""
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def with_no_window(kwargs: dict) -> dict:
    """The keyword arguments of a Popen, with «no window» added when nothing was asked for."""
    if os.name != "nt" or kwargs.get("creationflags"):
        return kwargs
    return {**kwargs, "creationflags": CREATE_NO_WINDOW}


def install() -> bool:
    """Patch `subprocess.Popen` (and so `run`, `check_output`, ...) once. False when there is nothing to do (not Windows, or done)."""
    if os.name != "nt" or getattr(subprocess.Popen, "_chief_nowindow", False):
        return False
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **with_no_window(kwargs))

    subprocess.Popen.__init__ = init
    subprocess.Popen._chief_nowindow = True
    return True
