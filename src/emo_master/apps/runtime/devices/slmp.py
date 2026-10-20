"""Runtime-owned SLMP session. No device I/O during construction or validation."""
import ipaddress
from threading import Event, Thread
import time
from uuid import uuid4

from emo_master.core.contracts.communication import plcDeviceAddressSpan
from emo_master.plugins.builtins._communication_operators import _slmpEndpoint
from emo_master.plugins.builtins._slmp import Slmp3ESession, SlmpResponseError
from .executor import DeviceError


from emo_master.core.contracts.device_driver import DeviceDriver


class ManagedSlmp(DeviceDriver):
    connectionRetryCodes = frozenset({"E_DEVICE_SLMP_IO"})

    def resetConnection(self):
        # Called only after a connect-only failure returned NOT_EXECUTED.
        self.close()
        self.faulted = False
        self.generation = None

    def __init__(self, definition):
        self.endpoint = _slmpEndpoint(definition.connection)
        self.session = Slmp3ESession(self.endpoint)
        self.connected = False
        self.generation = None
        self.faulted = False

    @staticmethod
    def physicalIdentity(definition):
        host = definition.connection["host"].strip().lower().rstrip(".")
        address = ipaddress.ip_address(host)
        host = (getattr(address, "ipv4_mapped", None) or address).compressed
        # Routing and frame format do not create a second physical PLC.
        return f"slmp://{host}:{definition.connection['port']}"

    def validateAction(self, action, arguments):
        if action == "connect":
            return
        if action not in {"read", "write"}:
            raise DeviceError("E_DEVICE_ACTION", "unsupported SLMP action")
        count = arguments["count"] if action == "read" else len(arguments["words"])
        maximum = 999999 if self.endpoint.frameEncoding == "ascii" else 0xFFFFFF
        bits = arguments.get("unit", "word") == "bit"
        if bits and (arguments["device"] != "M" or (action == "write" and any(v not in (0, 1) for v in arguments["words"]))):
            raise DeviceError("E_DEVICE_ACTION", "native bit requests require M and values 0/1")
        span = count if bits else plcDeviceAddressSpan(arguments["device"], count)
        if arguments["startAddress"] + span - 1 > maximum:
            raise DeviceError("E_DEVICE_ACTION", "SLMP action exceeds device address range")

    def execute(self, action, params, cancel, deadline, *, arguments, operationContext=None):
        if self.faulted:
            raise DeviceError("E_DEVICE_UNAVAILABLE", "SLMP session requires explicit recovery", unhealthy=True)
        self.validateAction(action, arguments)
        self._check(cancel, deadline)
        finished = Event()
        interrupted = Event()
        # Joined before releasing the executor: this closer can never close a
        # later operation's session or a newly recovered connection.
        def watch():
            while not finished.wait(.01):
                if cancel() or time.monotonic() >= deadline:
                    interrupted.set()
                    self.session.close()
                    return
        watcher = Thread(target=watch, name="slmp-operation-cancel", daemon=True)
        watcher.start()
        attempted = False
        try:
            if not self.connected:
                self.session.open()
                self.connected = True
                self.generation = uuid4().hex
            self._check(lambda: cancel() or interrupted.is_set(), deadline)
            if action == "connect":
                return dict(connectionGeneration=self.generation, outcome="COMPLETED")
            attempted = True
            bits = arguments.get("unit", "word") == "bit"
            if action == "read":
                words = (self.session.readBits(arguments["startAddress"], arguments["count"]) if bits else
                         self.session.readWords(arguments["device"], arguments["startAddress"], arguments["count"]))
                return dict(words=words, connectionGeneration=self.generation, outcome="COMPLETED")
            if bits:
                self.session.writeBits(arguments["startAddress"], [bool(v) for v in arguments["words"]])
            else:
                self.session.writeWords(arguments["device"], arguments["startAddress"], arguments["words"])
            return dict(connectionGeneration=self.generation, outcome="COMPLETED")
        except BaseException as error:
            self.connected = False
            self.session.close()
            if isinstance(error, DeviceError):
                raise
            self.faulted = True
            outcome = "REJECTED" if isinstance(error, SlmpResponseError) else "UNKNOWN" if attempted else "NOT_EXECUTED"
            raise DeviceError("E_DEVICE_SLMP_RESPONSE" if outcome == "REJECTED" else "E_DEVICE_SLMP_IO",
                              str(error), outcome=outcome, unhealthy=True) from error
        finally:
            finished.set()
            watcher.join()
            if interrupted.is_set():
                self.connected = False
                self.faulted = True

    def connect(self, params, cancelled, deadline):
        return self.execute("connect", params, cancelled, deadline, arguments={})

    @staticmethod
    def _check(cancel, deadline):
        if cancel():
            raise DeviceError("E_DEVICE_CANCELLED", "SLMP operation cancelled before exchange")
        if time.monotonic() >= deadline:
            raise DeviceError("E_DEVICE_OPERATION_TIMEOUT", "SLMP deadline expired before exchange")

    def close(self, deadline=None):
        self.session.close()
        self.connected = False

    def snapshot(self):
        return dict(ready=self.connected, ownsResources=self.connected, connectionState="FAULTED" if self.faulted else "CONNECTED" if self.connected else "DISCONNECTED",
                    connectionGeneration=self.generation)
