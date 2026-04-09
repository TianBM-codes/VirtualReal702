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


# Medium gray for elements that have no value (outside user-defined set)
_NEUTRAL_RGBA = np.array([160, 160, 160, 255], dtype=np.uint8)


def apply_jet_with_neutral(
    scalar_face: np.ndarray,
    val_min: float,
    val_max: float,
) -> np.ndarray:
    """
    Map scalar_face [Rf] (may contain NaN) to RGBA:
      NaN  → neutral gray  (element not in user-defined set)
      else → jet colormap, normalized to [val_min, val_max]

    Returns [Rf, 4] uint8.
    """
    nan_mask = np.isnan(scalar_face)
    span = val_max - val_min
    if span > 1e-12:
        normalized = np.clip((scalar_face - val_min) / span, 0.0, 1.0)
    else:
        # All values identical → show at jet midpoint (green-ish)
        normalized = np.where(nan_mask, 0.0, 0.5).astype(np.float32)

    # Replace NaN with 0 before LUT indexing (NaN → int cast triggers RuntimeWarning).
    # The nan_mask positions will be overwritten with neutral gray anyway.
    normalized_safe = np.where(nan_mask, 0.0, normalized).astype(np.float32)
    colors = apply_jet(normalized_safe)     # [Rf, 4] uint8
    colors[nan_mask] = _NEUTRAL_RGBA
    return colors
