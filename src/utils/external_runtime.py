"""Explicit integration boundary for separately installed file readers.

This module uses only the standard library and also runs in Python 2.7.
Provider names are deployment configuration, not bundled implementations.
"""
from __future__ import absolute_import

import importlib
import json
import os


def _setting(name):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    config_path = os.environ.get("FEM_RUNTIME_CONFIG", "").strip()
    if not config_path:
        return ""
    with open(config_path, "r") as stream:
        payload = json.load(stream)
    return str(payload.get(name) or "").strip()


def load_bulk_module(suffix):
    """Load a module from the configured, separately installed reader root."""
    root = _setting("FEM_BULK_PROVIDER_MODULE")
    if not root:
        raise ImportError(
            "BDF/OP2 reader is not configured. Install a compatible open-source "
            "provider separately and set FEM_BULK_PROVIDER_MODULE or FEM_RUNTIME_CONFIG."
        )
    return importlib.import_module(root + "." + suffix)


class BulkSymbol(object):
    """Defer optional reader imports until the corresponding feature is used."""

    def __init__(self, module, symbol):
        self.module = module
        self.symbol = symbol

    def _resolve(self):
        return getattr(load_bulk_module(self.module), self.symbol)

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._resolve(), name)


def load_result_constants():
    """Use a configured runtime constants module, or the result API exports."""
    module = _setting("FEM_RESULT_CONSTANTS_MODULE") or "odbAccess"
    return importlib.import_module(module)
