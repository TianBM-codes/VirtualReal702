#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Project-local pyNastran OP2 compatibility patches.

This module keeps fragile pyNastran workarounds in repo code instead of
requiring manual edits under site-packages for each environment.
"""

from __future__ import annotations

import inspect
from typing import Any


def apply_pynastran_op2_patches() -> None:
    """Apply idempotent runtime patches for known pyNastran OP2 issues."""
    from pyNastran.op2.result_objects.qualinfo import QualInfo

    if getattr(QualInfo, "_virtualreal702_patched", False):
        return

    original_from_str = QualInfo.from_str.__func__
    valid_keys = set(inspect.signature(QualInfo.__init__).parameters) - {"self"}

    def patched_from_str(cls, db_key: int, qual_str: str, log) -> Any:
        try:
            return original_from_str(cls, db_key, qual_str, log)
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise

        assert qual_str[0] == "(", f"qual_str={qual_str!r}"
        assert qual_str[-1] == ")", f"qual_str={qual_str!r}"
        qual_str2 = qual_str[1:-1]
        slines = qual_str2.split(";")

        data_dict = {}
        for sline in slines:
            key, value_str = sline.split("=", 1)
            if value_str == "FALSE":
                value = False
            elif value_str == "TRUE":
                value = True
            elif value_str == "' '":
                value = ""
            else:
                value_str = value_str.strip()
                if value_str.isdigit():
                    value = int(value_str)
                else:
                    value = value_str
            data_dict[key] = value

        ctor_data = {key: value for key, value in data_dict.items() if key in valid_keys}
        extra_data = {key: value for key, value in data_dict.items() if key not in valid_keys}

        qual_info = cls(**ctor_data)
        if extra_data:
            for key, value in extra_data.items():
                setattr(qual_info, key, value)
            setattr(qual_info, "_extra_fields", extra_data)
            if log is not None:
                log.warning(
                    "pyNastran QualInfo ignored unsupported field(s) %s for db_key=%s",
                    ", ".join(sorted(extra_data)),
                    db_key,
                )
        return qual_info

    QualInfo.from_str = classmethod(patched_from_str)
    QualInfo._virtualreal702_patched = True


def make_op2(*args, **kwargs):
    apply_pynastran_op2_patches()
    from pyNastran.op2.op2 import OP2

    return OP2(*args, **kwargs)


def read_op2_compat(*args, **kwargs):
    apply_pynastran_op2_patches()
    from pyNastran.op2.op2 import read_op2

    return read_op2(*args, **kwargs)


def read_op2_geom_compat(*args, **kwargs):
    apply_pynastran_op2_patches()
    from pyNastran.op2.op2_geom import read_op2_geom

    return read_op2_geom(*args, **kwargs)
