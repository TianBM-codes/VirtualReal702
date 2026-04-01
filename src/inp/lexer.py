"""
Layer 1: Lexer

Converts a raw INP file (possibly with *Include directives) into a flat list
of KeywordBlocks. Each block represents one Abaqus keyword invocation.

Responsibilities:
- Recursive *Include expansion (with cycle detection)
- Keyword continuation lines (keyword line ending with ',')
- Comment stripping ('**')
- Source location tracking (file path + line number)
- Output: List[KeywordBlock]

NOT responsible for semantic interpretation — that is the Parser's job.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Set, Tuple


@dataclass
class KeywordBlock:
    keyword: str              # normalised UPPER, e.g. "NODE", "ELEMENT", "END PART"
    params:  Dict[str, str]   # lowercase keys, original-case values, e.g. {"type": "C3D8R"}
    data_lines: List[str]     # raw data lines (stripped), between this keyword and the next
    source_file: str
    source_line: int          # line number of the '*Keyword' line (1-based)


def _parse_keyword_line(raw: str) -> Tuple[str, Dict[str, str]]:
    """
    Parse '*Keyword, key=val, key2=val2' into (KEYWORD, {key: val, ...}).
    Keyword name is uppercased; parameter keys are lowercased.
    """
    # Strip leading '*' and split on commas
    content = raw.lstrip("*").strip()
    parts = [p.strip() for p in content.split(",")]
    keyword = parts[0].upper().strip()

    params: Dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            k, _, v = part.partition("=")
            params[k.strip().lower()] = v.strip()
        elif part:
            # flag-style parameter (e.g. 'generate', 'internal')
            params[part.strip().lower()] = ""

    return keyword, params


def _iter_logical_lines(
    filepath: str,
    include_stack: List[str],
    visited: Set[str],
) -> Iterator[Tuple[str, str, int]]:
    """
    Yield (logical_line, source_file, source_line) tuples.

    Handles:
    - *Include recursion with cycle detection
    - Keyword line continuation (line ending with ',' that is a keyword line)
    """
    real_path = os.path.realpath(filepath)
    if real_path in visited:
        # Cycle — yield a sentinel comment that the caller can detect
        yield (f"**__INCLUDE_CYCLE__{filepath}", filepath, 0)
        return

    visited = visited | {real_path}
    include_stack = include_stack + [filepath]
    base_dir = os.path.dirname(filepath)

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        yield (f"**__INCLUDE_NOT_FOUND__{filepath}", filepath, 0)
        return

    i = 0
    while i < len(lines):
        raw = lines[i].rstrip("\n").rstrip("\r")
        lineno = i + 1
        i += 1

        stripped = raw.strip()

        # Empty line
        if not stripped:
            yield ("", filepath, lineno)
            continue

        # Pure comment
        if stripped.startswith("**"):
            continue

        # Keyword line
        if stripped.startswith("*"):
            # Handle *Include immediately
            kw, params = _parse_keyword_line(stripped)
            if kw == "INCLUDE":
                inc_file = params.get("input", "")
                if not os.path.isabs(inc_file):
                    inc_file = os.path.join(base_dir, inc_file)
                yield from _iter_logical_lines(inc_file, include_stack, visited)
                continue

            # Keyword continuation: if line ends with ',' the next non-comment
            # line is a continuation of the keyword parameters
            combined = stripped
            while combined.rstrip().endswith(","):
                # peek ahead for continuation
                while i < len(lines):
                    nxt = lines[i].strip()
                    i += 1
                    if nxt.startswith("**"):
                        continue  # skip comment lines
                    combined = combined.rstrip() + nxt
                    break
                else:
                    break

            yield (combined, filepath, lineno)
        else:
            # Data line
            yield (stripped, filepath, lineno)


def tokenize(filepath: str) -> Tuple[List[KeywordBlock], List[Tuple[str, str, int]]]:
    """
    Tokenize an INP file into a list of KeywordBlocks.

    Returns (blocks, raw_issues) where raw_issues contains special sentinel
    lines emitted by the include resolver (cycle / not-found).
    The caller (parser) converts raw_issues into Diagnostics.
    """
    blocks: List[KeywordBlock] = []
    issues: List[Tuple[str, str, int]] = []

    current_keyword: Optional[str] = None
    current_params:  Dict[str, str] = {}
    current_data:    List[str] = []
    current_file:    str = ""
    current_line:    int = 0

    def flush():
        if current_keyword is not None:
            blocks.append(KeywordBlock(
                keyword=current_keyword,
                params=dict(current_params),
                data_lines=list(current_data),
                source_file=current_file,
                source_line=current_line,
            ))

    for logical_line, src_file, src_lineno in _iter_logical_lines(
        filepath, [], set()
    ):
        # Sentinel lines from include resolver
        if logical_line.startswith("**__INCLUDE_CYCLE__"):
            issues.append(("CYCLE", logical_line[len("**__INCLUDE_CYCLE__"):], src_lineno))
            continue
        if logical_line.startswith("**__INCLUDE_NOT_FOUND__"):
            issues.append(("NOT_FOUND", logical_line[len("**__INCLUDE_NOT_FOUND__"):], src_lineno))
            continue

        # Empty line within data section — keep as separator for multiline
        # data blocks that use blank lines as separators (rare but valid)
        if not logical_line:
            continue

        if logical_line.startswith("*"):
            flush()
            kw, params = _parse_keyword_line(logical_line)
            current_keyword = kw
            current_params  = params
            current_data    = []
            current_file    = src_file
            current_line    = src_lineno
        else:
            if current_keyword is not None:
                current_data.append(logical_line)

    flush()
    return blocks, issues
