"""Optional Torch losses for the Gaussian worker; importing does not require Torch."""

from functools import lru_cache


@lru_cache(maxsize=8)
def _ssim_kernels(device, dtype):
    import torch

    axis = torch.arange(11, device=device, dtype=dtype) - 5
    gaussian = torch.exp(-(axis**2) / (2 * 1.5**2))
    gaussian /= gaussian.sum()
    # Five RGB moments, each convolved with the same separable Gaussian window.
    return (
        gaussian.view(1, 1, 1, 11).expand(15, 1, 1, 11).contiguous(),
        gaussian.view(1, 1, 11, 1).expand(15, 1, 11, 1).contiguous(),
    )


def masked_ssim_loss(prediction, target, window_weights):
    """Mean 1-SSIM on prevalidated full 11x11 windows, never on masked black RGB.

    window_weights comes from ssim_region_weights. Zero-weight windows do not
    contribute; excluded pixels have zero gradient because no accepted window
    includes them. All inputs use RGB in [0,1], in CHW/HW format.
    """
    import torch
    from torch.nn import functional as functional

    if prediction.shape != target.shape or prediction.shape[0] != 3:
        raise ValueError("SSIM requires matching CHW RGB tensors")
    if tuple(window_weights.shape) != tuple(prediction.shape[1:]):
        raise ValueError("SSIM window weights must match image geometry")
    if min(prediction.shape[1:]) < 11:
        return prediction.sum() * 0
    weight = window_weights[5:-5, 5:-5]
    if not bool(weight.any()):
        return prediction.sum() * 0
    horizontal, vertical = _ssim_kernels(prediction.device, prediction.dtype)
    moments = torch.cat(
        (prediction, target, prediction.square(), target.square(), prediction * target), dim=0
    ).unsqueeze(0)
    moments = functional.conv2d(moments, horizontal, groups=15)
    moments = functional.conv2d(moments, vertical, groups=15)
    mu_a, mu_b, aa, bb, ab = moments.split(3, dim=1)
    variance_a = (aa - mu_a.square()).clamp_min(0)
    variance_b = (bb - mu_b.square()).clamp_min(0)
    covariance = ab - mu_a * mu_b
    similarity = ((2 * mu_a * mu_b + 0.01**2) * (2 * covariance + 0.03**2)) / (
        (mu_a.square() + mu_b.square() + 0.01**2) * (variance_a + variance_b + 0.03**2)
    )
    return ((1 - similarity) * weight).sum() / (3 * weight.sum())


def densification_budget_mask(scores, threshold, budget):
    """Choose at most budget clone/split candidates before the upstream allocation.

    The classic implementation adds one net point for either a clone or a split
    into two children. Limiting candidates before that call bounds the live count.
    """
    import torch

    candidates = torch.isfinite(scores) & (scores >= threshold)
    count = int(candidates.sum())
    if budget <= 0:
        return torch.zeros_like(candidates)
    if count <= budget:
        return candidates
    selected = torch.zeros_like(candidates)
    ranked = torch.where(candidates, scores, -torch.inf).topk(budget).indices
    selected[ranked] = True
    return selected
