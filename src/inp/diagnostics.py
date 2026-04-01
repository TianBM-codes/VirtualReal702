"""
Diagnostic system for the INP parser.
All errors, warnings, and info messages are collected here rather than raised,
so parsing continues in a non-destructive way.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Literal


# Error codes
UNKNOWN_KEYWORD      = "UNKNOWN_KEYWORD"
UNSUPPORTED_PARAM    = "UNSUPPORTED_PARAM"
UNRESOLVED_SET       = "UNRESOLVED_SET"
UNRESOLVED_MATERIAL  = "UNRESOLVED_MATERIAL"
UNRESOLVED_INSTANCE  = "UNRESOLVED_INSTANCE"
UNRESOLVED_AMPLITUDE = "UNRESOLVED_AMPLITUDE"
DUPLICATE_NAME       = "DUPLICATE_NAME"
INCLUDE_CYCLE        = "INCLUDE_CYCLE"
INCLUDE_NOT_FOUND    = "INCLUDE_NOT_FOUND"
UNKNOWN_ELEMENT_TYPE = "UNKNOWN_ELEMENT_TYPE"
INVALID_FACE_ID      = "INVALID_FACE_ID"
INVALID_DOF          = "INVALID_DOF"
UMAT_SKIPPED         = "UMAT_SKIPPED"
MALFORMED_DATA       = "MALFORMED_DATA"
SET_CYCLE            = "SET_CYCLE"


@dataclass
class Diagnostic:
    severity: Literal["INFO", "WARNING", "ERROR"]
    code: str
    message: str
    file: str = ""
    line: int = 0
    include_stack: List[str] = field(default_factory=list)
    context: str = ""

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}" if self.file else "<unknown>"
        ctx = f" [{self.context}]" if self.context else ""
        return f"[{self.severity}] {self.code}{ctx} @ {loc}: {self.message}"


class DiagnosticCollector:
    def __init__(self) -> None:
        self._items: List[Diagnostic] = []

    def info(self, code: str, message: str, **kw) -> None:
        self._items.append(Diagnostic("INFO", code, message, **kw))

    def warning(self, code: str, message: str, **kw) -> None:
        self._items.append(Diagnostic("WARNING", code, message, **kw))

    def error(self, code: str, message: str, **kw) -> None:
        self._items.append(Diagnostic("ERROR", code, message, **kw))

    @property
    def items(self) -> List[Diagnostic]:
        return list(self._items)

    def has_errors(self) -> bool:
        return any(d.severity == "ERROR" for d in self._items)

    def summary(self) -> str:
        counts = {"INFO": 0, "WARNING": 0, "ERROR": 0}
        for d in self._items:
            counts[d.severity] += 1
        return (f"{counts['ERROR']} error(s), {counts['WARNING']} warning(s), "
                f"{counts['INFO']} info(s)")
