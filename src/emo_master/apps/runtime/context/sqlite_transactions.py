"""Bounded write admission shared by Runtime events and typed state.

Only BEGIN is retried. No SQL body, commit or side effect may be replayed.
The existing busy timeout bounds retry admission, not the transaction body or
unpreemptible native calls/scheduling. Zero still permits one immediate attempt.
No additional BEGIN starts after the retry deadline, even after oversleep.
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
    primary = None
    try:
        connection.execute("PRAGMA busy_timeout=0")
        lastBusy = None
        while True:
            if lastBusy is not None and time.monotonic() >= deadline:
                raise lastBusy
            try:
                connection.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                if not isBusy(error):
                    raise
                lastBusy = error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(remaining, random.uniform(.001, .005)))
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            connection.execute(f"PRAGMA busy_timeout={timeout}")
        except BaseException as restoreError:
            if primary is not None:
                # Both failures remain visible. Never continue a transaction
                # with an unconfirmed timeout configuration or replay its body.
                raise primary from restoreError
            raise
