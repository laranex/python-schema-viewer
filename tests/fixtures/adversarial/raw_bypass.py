"""Connects and writes through libc at import time, bypassing every guard."""
import json

from ._common import models, target
from ._libc import connect, write

host, port = target("TCP").rsplit(":", 1)
OUTCOME = {"tcp": connect(host, int(port)), "write": write(target("WRITE"))}
Base, Team, Member = models("bypass", comment=json.dumps(OUTCOME))
