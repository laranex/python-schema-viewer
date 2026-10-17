"""Writes through the raw sqlite3 driver at import time, bypassing every ORM."""
import sqlite3

from ._common import attempt, models, target


def write():
    connection = sqlite3.connect(target("SQLITE"))
    connection.execute("create table if not exists pwned (x)")
    connection.commit()
    connection.close()


OUTCOME = attempt(write)
Base, Team, Member = models("raw")
