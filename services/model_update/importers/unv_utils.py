from __future__ import annotations

from typing import Iterator, List, Tuple

from charset_normalizer import from_bytes


_FALLBACK_ENCODINGS = (
    "utf-8",
    "utf-8-sig",
    "gb18030",
    "gbk",
    "cp936",
    "cp1252",
    "latin-1",
)


def detect_unv_encoding(filename: str, sample_size: int = 512 * 1024) -> str:
    with open(filename, "rb") as fh:
        raw = fh.read(sample_size)
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"

    best = from_bytes(raw).best()
    if best is not None and best.encoding:
        return str(best.encoding)

    for encoding in _FALLBACK_ENCODINGS:
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _decode_unv_line(raw_line: bytes, primary_encoding: str) -> str:
    for encoding in (primary_encoding,) + _FALLBACK_ENCODINGS:
        try:
            return raw_line.decode(encoding).rstrip("\r\n")
        except UnicodeDecodeError:
            continue
    return raw_line.decode(primary_encoding, errors="replace").rstrip("\r\n")


def iter_unv_lines(filename: str) -> Iterator[str]:
    primary_encoding = detect_unv_encoding(filename)
    with open(filename, "rb") as fh:
        for raw_line in fh:
            yield _decode_unv_line(raw_line, primary_encoding)


def iter_unv_blocks(filename: str) -> Iterator[Tuple[str, List[str]]]:
    dataset_id = None
    records: List[str] = []
    inside_block = False

    for line in iter_unv_lines(filename):
        stripped = line.strip()

        if not inside_block:
            if stripped == "-1":
                inside_block = True
                dataset_id = None
                records = []
            continue

        if dataset_id is None:
            if not stripped or stripped == "-1":
                continue
            dataset_id = stripped
            continue

        if stripped == "-1":
            yield dataset_id, records
            inside_block = False
            dataset_id = None
            records = []
            continue

        records.append(line)


def read_unv_blocks(filename: str) -> List[Tuple[str, List[str]]]:
    return list(iter_unv_blocks(filename))


def list_unv_dataset_ids(filename: str) -> List[str]:
    return [dataset_id for dataset_id, _ in iter_unv_blocks(filename)]


def is_pyuff_available() -> bool:
    try:
        import pyuff  # noqa: F401
    except ImportError:
        return False
    return True
