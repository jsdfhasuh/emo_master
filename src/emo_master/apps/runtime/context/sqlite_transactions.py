"""Bounded write admission shared by Runtime events and typed state.

Only BEGIN is retried. No SQL body, commit or side effect may be replayed.
The connection's existing busy timeout remains the total admission budget.
"""
import random
import sqlite3
import time


def isBusy(error):
    return any(word in str(error).lower() for word in ("locked", "busy"))


def beginImmediate(connection):
    # Native busy backoff grows while other event producers can repeatedly
    # reacquire the writer between probes. Give every Runtime writer the same
    # short, jittered admission policy, rather than making variables alone yield.
    timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
    deadline = time.monotonic() + timeout / 1000
    connection.execute("PRAGMA busy_timeout=0")
    try:
        while True:
            try:
                connection.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                if not isBusy(error):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(remaining, random.uniform(.001, .005)))
    finally:
        connection.execute(f"PRAGMA busy_timeout={timeout}")
