"""Independent, standard-library-only TCP probe. Never connects outside 127/8.

Interactive JSON-lines commands on stdin: snapshot, pause, resume, send (hex),
close (rst: true/false), connect, quit. One JSON reply per command on stdout.
Without --interactive, observe for --duration seconds and print a final report.
TCP reads are byte chunks, NOT messages. Hash/count comparisons cover the stream.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import select
import socket
import struct
import sys
import threading
import time


def loopback(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_loopback:
        raise argparse.ArgumentTypeError("Only literal IPv4 loopback addresses (127/8) are allowed")
    return str(address)


class Probe:
    def __init__(self, args):
        self.args = args
        self.sock = None
        self.reading = not args.no_read
        self.nextRead = 0.0
        self.connections = []
        self.events = []
        self.digest = hashlib.sha256()
        self.prefix = bytearray()
        self.count = self.sent = 0

    def event(self, kind, **fields):
        self.events.append(dict(kind=kind, monotonic=time.monotonic(), **fields))
        self.events = self.events[-256:]

    def connect(self):
        if self.sock is not None:
            raise ValueError("Close the current connection before reconnecting")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(3)
            if self.args.receive_buffer:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.args.receive_buffer)
            sock.bind((self.args.source, 0))
            sock.connect((self.args.host, self.args.port))
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        self.sock = sock
        self.connections.append(dict(local=list(sock.getsockname()), peer=list(sock.getpeername()),
                                     receiveBuffer=sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)))
        self.event("connected", connection=len(self.connections))

    def close(self, rst=False):
        if self.sock is not None:
            if rst:
                fmt = "HH" if os.name == "nt" else "ii"
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(fmt, 1, 0))
            self.sock.close()
            self.sock = None
            self.event("closed", rst=rst)

    def tick(self):
        if self.sock is None or not self.reading or time.monotonic() < self.nextRead:
            return
        readable, _, _ = select.select([self.sock], [], [], 0)
        if not readable:
            return
        try:
            raw = self.sock.recv(self.args.read_chunk)
        except BlockingIOError:
            return
        except OSError as error:
            self.event("receive_error", error=str(error))
            self.close()
            return
        if not raw:
            self.event("eof")
            self.close()
            return
        self.count += len(raw)
        self.digest.update(raw)
        self.prefix.extend(raw[:max(0, 4096 - len(self.prefix))])
        self.nextRead = time.monotonic() + self.args.read_delay_ms / 1000

    def snapshot(self):
        return dict(pid=os.getpid(), connected=self.sock is not None, reading=self.reading,
                    receivedBytes=self.count, sha256=self.digest.hexdigest(), prefixHex=self.prefix.hex(),
                    sentBytes=self.sent, connections=self.connections, events=self.events)

    def command(self, command):
        action = command.get("action")
        if action == "connect":
            self.connect()
        elif action == "close":
            self.close(bool(command.get("rst")))
        elif action in {"pause", "resume"}:
            self.reading = action == "resume"
        elif action == "send":
            if self.sock is None:
                raise ValueError("Not connected")
            raw = bytes.fromhex(command["hex"])
            if len(raw) > 65536:
                raise ValueError("Probe TX limited to 64 KiB per command")
            # Deliberately no automatic retry after an error/unknown result.
            self.sock.settimeout(1)
            try:
                self.sock.sendall(raw)
                self.sent += len(raw)
            finally:
                self.sock.setblocking(False)
        elif action == "quit":
            self.close()
        elif action != "snapshot":
            raise ValueError(f"Unknown action: {action}")
        return dict(ok=True, action=action, **self.snapshot())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=loopback, default="127.0.0.1")
    parser.add_argument("--source", type=loopback, default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--no-read", action="store_true")
    parser.add_argument("--receive-buffer", type=int, default=0)
    parser.add_argument("--read-chunk", type=int, default=65536)
    parser.add_argument("--read-delay-ms", type=float, default=0)
    parser.add_argument("--expect-hex", help="Compare the complete received stream, not a recv chunk")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.read_chunk <= 1048576:
        parser.error("Invalid port or read chunk size")
    if args.duration <= 0 or args.read_delay_ms < 0 or args.receive_buffer < 0:
        parser.error("Durations must be positive and pacing/buffer nonnegative")
    expected = bytes.fromhex(args.expect_hex) if args.expect_hex is not None else None
    probe = Probe(args)
    commands = queue.Queue()

    def stdinReader():
        for line in sys.stdin:
            commands.put(line)
        commands.put('{"action":"quit"}')

    error = None
    try:
        probe.connect()
        if args.interactive:
            print(json.dumps(dict(ok=True, action="ready", **probe.snapshot())), flush=True)
            threading.Thread(target=stdinReader, daemon=True, name="probe-stdin").start()
        end = time.monotonic() + args.duration
        while args.interactive or time.monotonic() < end:
            probe.tick()
            if args.interactive:
                try:
                    line = commands.get(timeout=.002)
                except queue.Empty:
                    continue
                try:
                    command = json.loads(line)
                    reply = probe.command(command)
                except (ValueError, KeyError, OSError, TypeError) as failure:
                    reply = dict(ok=False, error=str(failure), **probe.snapshot())
                print(json.dumps(reply), flush=True)
                if reply.get("action") == "quit":
                    break
            else:
                time.sleep(.002)
    except (OSError, KeyboardInterrupt) as failure:
        error = str(failure)
    finally:
        probe.close()
    report = dict(ok=error is None, error=error, **probe.snapshot())
    if expected is not None:
        report["expectedMatch"] = (probe.count == len(expected)
                                   and probe.digest.hexdigest() == hashlib.sha256(expected).hexdigest())
        report["ok"] = report["ok"] and report["expectedMatch"]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not args.interactive or error:
        print(json.dumps(report), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
