"""
Jet colormap: normalized float [0,1] → RGBA uint8.
Pre-built as a 256-entry LUT for fast vectorized lookup.
"""
import numpy as np

# Build jet LUT once at import time
def _build_jet_lut(n: int = 256) -> np.ndarray:
    t = np.linspace(0.0, 1.0, n)
    r = np.clip(1.5 - np.abs(4 * t - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * t - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * t - 1), 0, 1)
    lut = np.stack([r, g, b, np.ones(n)], axis=1)   # [256, 4] float
    return (lut * 255).astype(np.uint8)

_JET_LUT = _build_jet_lut()


def apply_jet(normalized: np.ndarray) -> np.ndarray:
    """
    normalized : [N] float32, values in [0, 1]
    returns    : [N, 4] uint8 RGBA
    """
    idx = np.clip((normalized * 255).astype(np.int32), 0, 255)
    return _JET_LUT[idx]
