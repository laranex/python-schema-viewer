"""Connects to a TCP listener and resolves a host name at import time."""
import json
import socket

from ._common import attempt, models, target

host, port = target("TCP").rsplit(":", 1)


def connect():
    with socket.socket() as connection:
        connection.settimeout(2)
        connection.connect((host, int(port)))


OUTCOME = {"tcp": attempt(connect),
           "dns": attempt(lambda: socket.getaddrinfo("example.invalid", 80))}
Base, Team, Member = models("net", comment=json.dumps(OUTCOME))
