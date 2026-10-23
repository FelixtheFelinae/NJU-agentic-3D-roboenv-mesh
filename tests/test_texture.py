"""Texture completion must not turn missing or held-out RGB into observations."""

import numpy as np
import pytest
from PIL import Image

from roboenv_perception.construction.common import new_run, verify_run
from roboenv_perception.construction.table import complete_table
from roboenv_perception.construction.texture import (
    fill_texture_patches,
    repeat_texture_material,
    robust_texture_atlas,
)
from roboenv_perception.io import write_json


def test_median_rejects_occasional_highlights_and_ignores_unknown_rgb():
    base = np.full((12, 13, 3), [140, 100, 60], np.uint8)
    highlight = np.full_like(base, 250)
    masks = [np.ones(base.shape[:2], bool) for _ in range(3)]
    for mask in masks:
        mask[0, 0] = False
    atlas, count = robust_texture_atlas([base, base, highlight], masks, block_rows=3)
    np.testing.assert_array_equal(atlas[1:, 1:], base[1:, 1:])
    np.testing.assert_array_equal(atlas[0, 0], 0)
    assert count[0, 0] == 0 and count[1, 1] == 3


def test_simple_material_uses_only_the_observed_donor_and_marks_remapping_synthetic():
    atlas = np.zeros((20, 24, 3), np.uint8)
    atlas[3:13, 4:10] = np.arange(180).reshape(10, 6, 3)
    observed = np.zeros(atlas.shape[:2], bool)
    observed[3:13, 4:10] = True
    material, details = repeat_texture_material(atlas, observed, (4, 3, 10, 13))
    np.testing.assert_array_equal(material[3:13, 4:10], atlas[3:13, 4:10])
    donor_colors = set(map(tuple, atlas[observed]))
    assert all(tuple(color) in donor_colors for color in material.reshape(-1, 3))
    assert details["synthetic_fraction"] == 1 and not details["location_specific_true_texture"]
    with pytest.raises(ValueError, match="unknown"):
        repeat_texture_material(atlas, observed, (3, 3, 10, 13))


@pytest.mark.parametrize("touch_edge", [False, True])
def test_patch_fill_is_independent_of_unknown_rgb_and_preserves_observations(touch_edge):
    y, x = np.indices((48, 52))
    wood = np.stack([120 + x % 5 * 9, 70 + x % 5 * 7, 35 + x % 5 * 4], axis=-1)
    wood = wood.astype(np.uint8)
    valid = np.ones(wood.shape[:2], bool)
    valid[0 if touch_edge else 14:33, 17:34] = False
    first, second = wood.copy(), wood.copy()
    first[~valid] = [255, 0, 255]
    second[~valid] = [0, 255, 0]
    a, metadata = fill_texture_patches(first, valid, patch_size=11, blend_pixels=0)
    b, _ = fill_texture_patches(second, valid, patch_size=11, blend_pixels=0)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(a[valid], wood[valid])
    donor_colors = set(map(tuple, wood[valid]))
    assert all(tuple(color) in donor_colors for color in a[~valid])
    assert metadata["patches_copied"] > 0


def test_seam_blend_changes_only_holes_and_empty_donor_fails():
    rng = np.random.default_rng(13)
    atlas = rng.integers(40, 180, size=(30, 32, 3), dtype=np.uint8)
    valid = np.ones(atlas.shape[:2], bool)
    valid[10:20, 10:20] = False
    result, _ = fill_texture_patches(atlas, valid, patch_size=7)
    np.testing.assert_array_equal(result[valid], atlas[valid])
    with pytest.raises(ValueError, match="no observed"):
        fill_texture_patches(atlas, np.zeros_like(valid))
    with pytest.raises(ValueError, match="3x3"):
        fill_texture_patches(atlas, np.indices(valid.shape).sum(axis=0) % 2 == 0)


def test_patch_table_stage_never_fuses_holdout_pixels(tmp_path):
    background = tmp_path / "background"
    with new_run(background, "fixture", {}):
        frames = []
        for i in range(4):
            rgb = np.full((32, 32, 3), [220, 0, 220] if i == 0 else [140, 100, 60], np.uint8)
            if i == 3:
                rgb[:10] = 250  # A single training-view highlight must be suppressed.
            Image.fromarray(rgb).save(background / f"{i}.png")
            valid = np.full((32, 32), 255, np.uint8)
            valid[12:20, 12:20] = 0
            Image.fromarray(valid).save(background / f"mask{i}.png")
            frames.append({
                "frame_id": str(i), "image_name": f"{i}.png", "image_ref": f"{i}.png",
                "size": [32, 32], "observed_mask_ref": f"mask{i}.png",
                "split": "holdout" if i == 0 else "train",
            })
        write_json(background / "background.json", {"frames": frames})
    spec = tmp_path / "table.json"
    write_json(spec, {
        "width_m": 1, "depth_m": 1, "pixels_per_metre": 32,
        "fill": "patch", "fusion": "median", "patch_size": 7,
        "views": [
            {"frame_id": str(i), "corners_xy": [[0, 0], [31, 0], [31, 31], [0, 31]]}
            for i in range(4)
        ],
    })
    output = tmp_path / "table"
    complete_table(background, spec, output)
    result = verify_run(output, "completion.json")
    atlas = np.asarray(Image.open(output / "atlas.png"))
    np.testing.assert_array_equal(atlas[:10], np.broadcast_to([140, 100, 60], (10, 32, 3)))
    assert atlas[..., 0].max() < 150 and atlas[..., 1].min() > 90
    assert result["fill_details"]["patches_copied"] > 0
    assert result["holdout_pixels_used_for_fusion"] is False
    provenance = np.asarray(Image.open(output / "provenance.png"))
    assert provenance[15, 15] == 2 and provenance[0, 0] == 1
