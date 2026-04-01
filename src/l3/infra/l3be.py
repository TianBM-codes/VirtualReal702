"""
L3 Binary Envelope v1 (L3BE) encoder.
Layout: [Fixed Header 40B][Section Table N×80B][Section Payloads...]
Spec: docs/l3/Binary-Payload-Spec.md
"""
import struct
import numpy as np
from typing import List, Tuple

MAGIC = b"L3BE"
VERSION = 1
HEADER_SIZE = 40
SECTION_ENTRY_SIZE = 80

DTYPE_CODE = {
    "int8":    1,  "uint8":   2,
    "int16":   3,  "uint16":  4,
    "int32":   5,  "uint32":  6,
    "int64":   7,  "uint64":  8,
    "float32": 9,  "float64": 10,
}


def _align8(n: int) -> int:
    return (n + 7) & ~7


def build(sections: List[Tuple[str, np.ndarray]]) -> bytes:
    """
    sections: list of (name, ndarray)
    Returns: bytes — complete L3BE v1 payload
    """
    n = len(sections)
    payload_offset = _align8(HEADER_SIZE + n * SECTION_ENTRY_SIZE)

    # Pre-compute section offsets
    offsets = []
    cursor = payload_offset
    for _, arr in sections:
        offsets.append(cursor)
        cursor = _align8(cursor + arr.nbytes)
    total_size = cursor

    buf = bytearray(total_size)

    # Fixed Header (40 bytes)
    struct.pack_into(
        "<4sHHIIII16x",   # magic, version, flags, header_size, section_count,
        buf, 0,           # section_table_offset, payload_offset, reserved(16B)
        MAGIC, VERSION, 0, HEADER_SIZE, n,
        HEADER_SIZE, payload_offset,
    )

    # Section Table
    for i, ((name, arr), offset) in enumerate(zip(sections, offsets)):
        entry_offset = HEADER_SIZE + i * SECTION_ENTRY_SIZE
        name_bytes = name.encode("ascii")[:32].ljust(32, b"\x00")
        dtype_code = DTYPE_CODE.get(arr.dtype.name, 9)
        ndim = arr.ndim
        shape = list(arr.shape) + [0] * (4 - ndim)   # pad to 4 dims

        struct.pack_into(
            "<32sHH4I QQ I4x",   # name, dtype_code, ndim, shape[4],
            buf, entry_offset,   # offset, nbytes, flags, reserved
            name_bytes, dtype_code, ndim,
            shape[0], shape[1], shape[2], shape[3],
            offset, arr.nbytes,
            0,
        )

    # Section Payloads
    for (_, arr), offset in zip(sections, offsets):
        raw = np.ascontiguousarray(arr).tobytes()
        buf[offset: offset + len(raw)] = raw

    return bytes(buf)
