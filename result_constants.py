"""Constants exported by the external result-reading runtime (Python 2.7+)."""
from src.utils.external_runtime import load_result_constants

_provider = load_result_constants()
__all__ = [name for name in dir(_provider) if name.isupper()]
globals().update((name, getattr(_provider, name)) for name in __all__)
