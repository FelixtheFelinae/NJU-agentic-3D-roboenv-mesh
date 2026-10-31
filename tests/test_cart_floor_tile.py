import numpy as np
import pytest

from experiments.cart_background.tile import choose_patch, mirrored_tile


def test_unobserved_material_is_rejected():
    with pytest.raises(ValueError):
        choose_patch(np.full((192, 192, 3), 160, np.uint8), np.zeros((192, 192), bool))


def test_patch_cannot_contain_unobserved_texels():
    rgb = np.full((192, 192, 3), 160, np.uint8)
    mask = np.ones((192, 192), bool)
    mask[:80, :80] = False
    _, x, y, n = choose_patch(rgb, mask)
    assert mask[y : y + n, x : x + n].all()


def test_mirrored_tile_preserves_pixels_and_matches_wrap_boundaries():
    patch = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
    tile = mirrored_tile(patch)
    assert np.array_equal(tile[:4, :5], patch)
    assert np.array_equal(tile[0], tile[-1])
    assert np.array_equal(tile[:, 0], tile[:, -1])
