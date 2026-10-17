"""Starts a process at import time that would write a file after the build has moved on."""
import subprocess
import sys

from ._common import attempt, models, target

code = f"import time, pathlib; time.sleep(2); pathlib.Path({target('WRITE')!r}).write_text('late write')"
OUTCOME = attempt(lambda: subprocess.Popen([sys.executable, "-c", code]))
Base, Team, Member = models("spawn")
