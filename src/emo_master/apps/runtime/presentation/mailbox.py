"""Single-writer/single-reader fixed shared-memory mailbox for trusted Workers.

No Pipe recv or queue feeder can wait forever on a partially written large frame
after force kill. A ready byte publishes length+CRC+payload; an interrupted write
stays WRITING until the Supervisor fence retires this Job's entire mailbox.
"""
import pickle
import queue
import struct
import time
import zlib


class SharedMailbox:
    slots = 16
    capacity = 1024 * 1024 + 64 * 1024

    def __init__(self, context):
        self.data = context.Array("B", self.slots * (self.capacity + 16), lock=False)
        self.states = context.Array("B", self.slots, lock=False)
        self.writeIndex = 0
        self.readIndex = 0

    def put_nowait(self, value):
        index = self.writeIndex % self.slots
        if self.states[index]:
            raise queue.Full
        content = pickle.dumps(value, protocol=5)
        if len(content) > self.capacity:
            raise ValueError("display IPC packet budget exceeded")
        self.states[index] = 1
        offset = index * (self.capacity + 16)
        memory = memoryview(self.data).cast("B")
        memory[offset:offset + 16] = struct.pack("<QII", self.writeIndex, len(content), zlib.crc32(content))
        memory[offset + 16:offset + 16 + len(content)] = content
        self.states[index] = 2
        self.writeIndex += 1

    def get(self, timeout):
        deadline = time.perf_counter() + timeout
        index = self.readIndex % self.slots
        while self.states[index] != 2:
            if time.perf_counter() >= deadline:
                raise queue.Empty
            time.sleep(.001)
        offset = index * (self.capacity + 16)
        memory = memoryview(self.data).cast("B")
        sequence, size, checksum = struct.unpack("<QII", memory[offset:offset + 16])
        try:
            if sequence != self.readIndex or size > self.capacity:
                raise ValueError("invalid IPC frame length")
            content = bytes(memory[offset + 16:offset + 16 + size])
            if zlib.crc32(content) != checksum:
                raise ValueError("invalid IPC frame checksum")
            return pickle.loads(content)
        finally:
            self.states[index] = 0
            self.readIndex += 1
