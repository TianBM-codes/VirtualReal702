from typing import Any

import numpy as np


def _expand_scatter(r_scatter: Any, size: int) -> np.ndarray:
    scatter = np.asarray(r_scatter, dtype=float).reshape(-1)
    if scatter.size == 1:
        scatter = np.full(int(size), float(scatter[0]), dtype=float)
    if scatter.size != int(size):
        raise ValueError("r_scatter size mismatch with response size")
    return scatter


def build_ccmean(r_exp, r_ana, r_scatter, eps: float = 1.0e-30) -> float:
    r_exp = np.asarray(r_exp, dtype=float).reshape(-1)
    r_ana = np.asarray(r_ana, dtype=float).reshape(-1)
    if r_exp.size != r_ana.size:
        raise ValueError("r_exp and r_ana size mismatch")

    scatter = _expand_scatter(r_scatter, r_exp.size)
    rel_diff = (r_ana - r_exp) / np.maximum(np.abs(r_exp), float(eps))
    return float(np.sum(rel_diff / np.maximum(scatter, float(eps))))


def build_ccdis(r_exp, r_ana, r_scatter, eps: float = 1.0e-30) -> float:
    r_exp = np.asarray(r_exp, dtype=float).reshape(-1)
    r_ana = np.asarray(r_ana, dtype=float).reshape(-1)
    if r_exp.size != r_ana.size:
        raise ValueError("r_exp and r_ana size mismatch")

    scatter = _expand_scatter(r_scatter, r_exp.size)
    rel_diff = np.abs(r_ana - r_exp) / np.maximum(np.abs(r_exp), float(eps))
    weights = 1.0 / np.maximum(np.abs(scatter), float(eps)) ** 2
    total_weight = float(np.sum(weights))
    if total_weight <= float(eps):
        raise ValueError("sum of weights C_R is too small or zero")
    return float(np.sum(weights * rel_diff) / total_weight)


def build_cctot(ccabs: float, ccdis: float) -> float:
    return float(float(ccabs) + float(ccdis))
