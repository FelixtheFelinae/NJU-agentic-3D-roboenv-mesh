"""Gradient and provenance checks for masked structural supervision."""

import numpy as np
import pytest

from roboenv_perception.construction.contracts import BackgroundConfig
from roboenv_perception.construction.supervision import ssim_region_weights
from roboenv_perception.construction.training_loss import (
    densification_budget_mask,
    masked_ssim_loss,
)


def test_ssim_windows_do_not_cross_unknown_synthetic_or_image_boundaries():
    observed = np.zeros((35, 55), dtype=bool)
    synthetic = observed.copy()
    observed[:, :25] = True
    synthetic[:, 30:] = True
    weights = ssim_region_weights(observed, synthetic, 0.15)
    assert weights[17, 12] == 1
    assert weights[17, 42] == np.float32(0.15)
    assert weights[17, 19] == 1 and weights[17, 20] == 0
    assert not weights[:5].any() and not weights[-5:].any()
    assert not weights[:, 20:35].any()
    # Adjacent real/synthetic regions also cannot share a structural window.
    synthetic[:, 25:] = True
    weights = ssim_region_weights(observed, synthetic, 0.15)
    assert not weights[:, 20:30].any()


def test_small_and_invalid_ssim_masks():
    small = np.ones((5, 5), dtype=bool)
    assert not ssim_region_weights(small, ~small, 0.15).any()
    with pytest.raises(ValueError, match="odd"):
        ssim_region_weights(small, ~small, 0.15, window_size=4)
    with pytest.raises(ValueError, match="disjoint"):
        ssim_region_weights(small, small, 0.15)


@pytest.fixture
def torch_cpu():
    torch = pytest.importorskip("torch")
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield torch
    torch.set_num_threads(threads)


def test_excluded_rgb_cannot_change_ssim_loss_or_receive_gradient(torch_cpu):
    torch = torch_cpu
    observed = np.zeros((25, 35), dtype=bool)
    observed[2:23, 2:33] = True
    weights = torch.from_numpy(ssim_region_weights(observed, np.zeros_like(observed), 0.15))
    prediction = torch.full((3, 25, 35), 0.2, requires_grad=True)
    truth = torch.full_like(prediction, 0.4)
    changed = truth.clone()
    changed[:, ~torch.from_numpy(observed)] = 50
    original_loss = masked_ssim_loss(prediction, truth, weights)
    changed_loss = masked_ssim_loss(prediction, changed, weights)
    torch.testing.assert_close(original_loss, changed_loss, atol=0, rtol=0)
    changed_loss.backward()
    assert torch.count_nonzero(prediction.grad[:, ~torch.from_numpy(observed)]) == 0
    assert torch.count_nonzero(prediction.grad[:, torch.from_numpy(observed)]) > 0


def test_synthetic_ssim_uses_lower_weight_without_mixing_regions(torch_cpu):
    torch = torch_cpu
    observed = np.zeros((31, 71), dtype=bool)
    synthetic = observed.copy()
    observed[:, :31] = True
    synthetic[:, 40:] = True
    weights = torch.from_numpy(ssim_region_weights(observed, synthetic, 0.1)).double()
    prediction = torch.full((3, 31, 71), 0.5, dtype=torch.float64)
    prediction[:, :, 40:] = 0
    truth = torch.full_like(prediction, 0.5)
    loss = masked_ssim_loss(prediction, truth, weights)
    # Equal window counts in real/synthetic regions. Only the synthetic part errs.
    expected = (1 - 0.0001 / (0.25 + 0.0001)) * 0.1 / 1.1
    assert float(loss) == pytest.approx(expected, abs=1e-7)


def test_empty_ssim_support_has_finite_zero_loss_and_gradient(torch_cpu):
    torch = torch_cpu
    prediction = torch.rand(3, 13, 13, requires_grad=True)
    loss = masked_ssim_loss(prediction, torch.zeros_like(prediction), torch.zeros(13, 13))
    assert float(loss.detach()) == 0
    loss.backward()
    assert torch.count_nonzero(prediction.grad) == 0


def test_density_budget_keeps_only_supported_highest_finite_gradients(torch_cpu):
    torch = torch_cpu
    scores = torch.tensor([0.1, 0.2, 0.7, 0.8, float("nan"), float("inf")])
    assert densification_budget_mask(scores, 0.2, 2).tolist() == [
        False,
        False,
        True,
        True,
        False,
        False,
    ]
    assert not densification_budget_mask(scores, 0.2, 0).any()
    assert int(densification_budget_mask(scores, 0.2, 99).sum()) == 3


def test_invalid_adaptive_schedules_fail_before_starting_a_worker():
    with pytest.raises(ValueError, match="exceed"):
        BackgroundConfig(densify_from_iter=1000, densify_until_iter=500)
    with pytest.raises(ValueError, match="unique"):
        BackgroundConfig(checkpoint_iterations=[3000, 3000])
    with pytest.raises(ValueError, match="positive"):
        BackgroundConfig(checkpoint_iterations=[0])
