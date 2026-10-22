"""Packet-independent polynomial CDC with one modular reduction per byte."""
from collections import deque

from governance_contracts import Chunk


class RabinCDCScanner:
    """Polynomial rolling CDC (Rabin–Karp style, not a GF(2) Rabin fingerprint).

    Hash the last W bytes in base 256 modulo 2**61-1. A separate boundary
    modulus controls cuts. feed() yields chunks independent of packetization;
    finish() emits the tail. Chunks remain bytes, even across UTF-8 characters.
    The rolling window continues across cuts, helping content resynchronize.
    """

    def __init__(self, block_size_target=512, window_size=64,
                 min_size=None, max_size=None, target=1):
        if type(block_size_target) is not int or block_size_target < 2:
            raise ValueError("block_size_target must be an integer >= 2")
        if type(window_size) is not int or window_size < 1:
            raise ValueError("window_size must be a positive integer")
        self.modulus = block_size_target
        self.window_size = window_size
        self.min_size = max(window_size, block_size_target // 4) if min_size is None else min_size
        self.max_size = block_size_target * 4 if max_size is None else max_size
        if not (type(self.min_size) is int and type(self.max_size) is int
                and window_size <= self.min_size <= self.max_size):
            raise ValueError("Require window_size <= min_size <= max_size")
        if type(target) is not int or not 0 <= target < self.modulus:
            raise ValueError("target must be inside the boundary modulus")
        self.target = target
        self.base = 256
        self.field_prime = (1 << 61) - 1
        self._remove_factor = pow(self.base, window_size - 1, self.field_prime)
        removal = self._remove_factor * self.base % self.field_prime
        self._remove_table = tuple(byte * removal % self.field_prime for byte in range(256))
        self._boundary_mask = self.modulus - 1 if self.modulus & (self.modulus - 1) == 0 else None
        self._window = deque()
        self._buffer = bytearray()
        self._fingerprint = 0
        self._offset = 0
        self._chunk_start = 0
        self._index = 0
        self._finished = False

    def _cut(self, reason):
        chunk = Chunk(self._index, self._chunk_start, self._offset,
                      bytes(self._buffer), self._fingerprint, reason)
        self._buffer.clear()
        self._chunk_start = self._offset
        self._index += 1
        return chunk

    def feed(self, data):
        if self._finished:
            raise RuntimeError("Cannot feed after finish()")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("feed() requires bytes; encode text once at the boundary")
        view = memoryview(data).cast("B")
        window, buffer = self._window, self._buffer
        append, pop = window.append, window.popleft
        fingerprint, offset = self._fingerprint, self._offset
        size, segment, processed = len(buffer), 0, 0
        width, prime, remove = self.window_size, self.field_prime, self._remove_table
        minimum, maximum = self.min_size, self.max_size
        mask, modulus, target = self._boundary_mask, self.modulus, self.target
        try:
            for position, byte in enumerate(view):
                subtraction = remove[pop()] if len(window) == width else 0
                fingerprint = ((fingerprint << 8) + byte - subtraction) % prime
                append(byte)
                offset += 1
                size += 1
                processed = position + 1
                if size < minimum:
                    continue
                remainder = fingerprint & mask if mask is not None else fingerprint % modulus
                if remainder == target or size >= maximum:
                    buffer.extend(view[segment:processed])
                    segment, size = processed, 0
                    self._fingerprint, self._offset = fingerprint, offset
                    yield self._cut("content" if remainder == target else "max size")
        finally:
            buffer.extend(view[segment:processed])
            self._fingerprint, self._offset = fingerprint, offset

    def finish(self):
        if self._finished:
            return []
        self._finished = True
        return [self._cut("tail")] if self._buffer else []

    def chunk_stream(self, text_stream):
        """Convenience API for a fresh scanner; byte offsets are authoritative."""
        return [*self.feed(text_stream.encode("utf-8")), *self.finish()]
