"""Shared NumPy supervision contract, independent of CUDA dependencies."""

import numpy as np


def region_weights(observed, synthetic, synthetic_weight):
    if observed.shape != synthetic.shape or np.any(observed & synthetic):
        raise ValueError("observed and synthetic masks must be disjoint with matching shapes")
    if not 0 < synthetic_weight < 1:
        raise ValueError("synthetic weight must be strictly between zero and one")
    return observed.astype(np.float32) + synthetic.astype(np.float32) * synthetic_weight


def projection_matrix(intrinsics, size, near=0.01, far=100.0):
    """Column-vector +Z-forward projection including off-centre principal points."""
    k = np.asarray(intrinsics, dtype=float)
    width, height = size
    if k.shape != (3, 3) or not np.isfinite(k).all() or width <= 0 or height <= 0:
        raise ValueError("invalid pinhole camera")
    if k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1]):
        raise ValueError("invalid focal length or homogeneous intrinsics")
    if not np.isclose(k[0, 1], 0) or not np.isclose(k[1, 0], 0):
        raise ValueError("skewed camera intrinsics are unsupported")
    return np.array(
        [
            [2 * k[0, 0] / width, 0, 2 * k[0, 2] / width - 1, 0],
            [0, 2 * k[1, 1] / height, 2 * k[1, 2] / height - 1, 0],
            [0, 0, far / (far - near), -far * near / (far - near)],
            [0, 0, 1, 0],
        ],
        dtype=np.float32,
    )
