"""Shared NumPy supervision contract, independent of CUDA dependencies."""

import numpy as np


def region_weights(observed, synthetic, synthetic_weight):
    if observed.shape != synthetic.shape or np.any(observed & synthetic):
        raise ValueError("observed and synthetic masks must be disjoint with matching shapes")
    if not 0 < synthetic_weight < 1:
        raise ValueError("synthetic weight must be strictly between zero and one")
    return observed.astype(np.float32) + synthetic.astype(np.float32) * synthetic_weight


def ssim_region_weights(observed, synthetic, synthetic_weight, window_size=11):
    """Only windows entirely within one provenance region receive SSIM supervision.

    Real/synthetic boundaries and image edges are excluded, as are unknown pixels.
    This integral-image erosion keeps this contract independent of Torch/OpenCV.
    """
    region_weights(observed, synthetic, synthetic_weight)
    if observed.ndim != 2 or window_size < 1 or window_size % 2 != 1:
        raise ValueError("SSIM requires 2D masks and an odd positive window size")

    def interior(mask):
        radius = window_size // 2
        padded = np.pad(mask.astype(np.int64), radius)
        integral = np.pad(padded, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        counts = (
            integral[window_size:, window_size:]
            - integral[:-window_size, window_size:]
            - integral[window_size:, :-window_size]
            + integral[:-window_size, :-window_size]
        )
        return counts == window_size**2

    return (
        interior(observed).astype(np.float32)
        + interior(synthetic).astype(np.float32) * synthetic_weight
    )


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
