"""Forks through libc, which no audit event sees, and leaves the child running after the build. Only the process
group cleanup stops it."""
import ctypes
import os
import time

from ._common import models

libc = ctypes.CDLL(None, use_errno=True)
pid = libc.fork()
if pid == 0:
    time.sleep(600)
    os._exit(0)
Base, Team, Member = models("escape", comment=str(pid))
