"""Appearance-only planar fusion and exemplar filling, with immutable observed pixels."""

import numpy as np

from .common import cv2_module


def repeat_texture_material(atlas, observed, donor_rect):
    """Make an approximate material by mirror-repeating a fully observed RGB patch.

    All output surface assignments are synthetic: real donor RGB does not establish
    the true texture at its new location. This deliberately replaces baked shadows.
    """
    atlas, observed = np.asarray(atlas), np.asarray(observed, dtype=bool)
    if atlas.dtype != np.uint8 or atlas.shape != (*observed.shape, 3):
        raise ValueError("uint8 RGB atlas and matching 2D mask required")
    x0, y0, x1, y1 = donor_rect
    h, w = observed.shape
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError("donor rectangle must lie inside the atlas")
    if not observed[y0:y1, x0:x1].all():
        raise ValueError("material donor contains unknown pixels")
    patch = atlas[y0:y1, x0:x1]
    result = np.pad(patch, ((y0, h-y1), (x0, w-x1), (0, 0)), mode="reflect")
    return result, {
        "method": "mirror repeat of one fully observed training-atlas patch",
        "donor_atlas_rect_xyxy": list(donor_rect),
        "synthetic_fraction": 1.0,
        "location_specific_true_texture": False,
    }


def robust_texture_atlas(images, masks, block_rows=64):
    """Median of training observations; missing pixels never vote as black.

    The caller must supply only training images in a common planar coordinate system.
    A median suppresses occasional highlights; it cannot recover intrinsic albedo.
    Row blocks bound the temporary floating-point allocation.
    """
    if not images or len(images) != len(masks):
        raise ValueError("one validity mask is required per image")
    shape = images[0].shape
    if len(shape) != 3 or shape[2] != 3:
        raise ValueError("RGB images required")
    if any(im.shape != shape for im in images) or any(m.shape != shape[:2] for m in masks):
        raise ValueError("atlas images and masks must share a shape")
    if block_rows < 1:
        raise ValueError("block_rows must be positive")
    atlas = np.zeros(shape, np.uint8)
    count = np.zeros(shape[:2], np.uint16)
    for y in range(0, shape[0], block_rows):
        section = slice(y, y + block_rows)
        values = np.stack([im[section] for im in images]).astype(np.float32)
        valid = np.stack([m[section] for m in masks]).astype(bool)
        votes = valid.sum(axis=0)
        # Sorting masked values to +inf avoids all-NaN warnings in unknown regions.
        values[~valid] = np.inf
        values.sort(axis=0)
        lo = np.maximum(votes - 1, 0) // 2
        hi = votes // 2
        lower = np.take_along_axis(values, lo[None, ..., None], axis=0)[0]
        upper = np.take_along_axis(values, hi[None, ..., None], axis=0)[0]
        selected = np.zeros_like(lower)
        np.add(lower, upper, out=selected, where=(votes > 0)[..., None])
        atlas[section] = np.clip(selected * 0.5, 0, 255).astype(np.uint8)
        count[section] = votes
    return atlas, count


def fill_texture_patches(atlas, observed, patch_size=41, blend_pixels=5):
    """Fill holes by translating fully observed donor patches without rotating grain.

    Match each frontier patch against immutable real donors using its known context.
    Synthesized pixels may guide the next match but can never become donor texture.
    Observed RGB is preserved exactly, including during the interior seam blend.
    """
    cv2 = cv2_module()
    atlas = np.asarray(atlas)
    observed = np.asarray(observed, dtype=bool)
    if atlas.dtype != np.uint8 or atlas.shape != (*observed.shape, 3):
        raise ValueError("uint8 RGB atlas and matching 2D mask required")
    if patch_size < 3 or patch_size % 2 != 1:
        raise ValueError("patch_size must be odd and at least 3")
    if not observed.any():
        raise ValueError("no observed donor texture")
    output = atlas.copy()
    holes = ~observed
    if not holes.any():
        return output, {"patch_size": patch_size, "patches_copied": 0}
    h, w = observed.shape
    patch_size = min(patch_size, (min(h, w) - 1) // 2 * 2 + 1)
    # Fit the donor footprint to available observations, never to synthesized pixels.
    while patch_size >= 3:
        donors = cv2.boxFilter(
            observed.astype(np.float32), -1, (patch_size, patch_size),
            normalize=False, borderType=cv2.BORDER_CONSTANT,
        ) >= patch_size**2 - 0.1
        if donors.any():
            break
        patch_size -= 2
    if patch_size < 3:
        raise ValueError("no fully observed 3x3 donor patch; capture more texture")
    radius = patch_size // 2
    # Template matching returns coordinates of patch top-left corners.
    donor_valid = donors[radius:h-radius, radius:w-radius]
    source = atlas.astype(np.float32) / 255
    known = observed.copy()
    copies = 0
    while not known.all():
        frontier = (~known) & (cv2.dilate(known.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
        context = cv2.boxFilter(
            known.astype(np.float32), -1, (patch_size, patch_size),
            normalize=False, borderType=cv2.BORDER_CONSTANT,
        )
        # Prefer well-supported frontier patches. A deterministic tie break is sufficient.
        context[~frontier] = -1
        y, x = np.unravel_index(np.argmax(context), context.shape)
        y0 = min(max(y - radius, 0), h - patch_size)
        x0 = min(max(x - radius, 0), w - patch_size)
        region = np.s_[y0:y0+patch_size, x0:x0+patch_size]
        support = known[region].astype(np.float32)
        target = output[region].astype(np.float32) / 255
        costs = cv2.matchTemplate(source, target, cv2.TM_SQDIFF, mask=support)
        costs[~donor_valid] = np.inf
        costs[~np.isfinite(costs)] = np.inf
        sy, sx = np.unravel_index(np.argmin(costs), costs.shape)
        if not np.isfinite(costs[sy, sx]):
            raise ValueError("no finite donor match")
        missing = ~known[region]
        output[region][missing] = atlas[sy:sy+patch_size, sx:sx+patch_size][missing]
        known[region] = True
        copies += 1
    if blend_pixels > 0:
        # Blend only the inner rim of the hole; never alter genuine observations.
        rim = cv2.distanceTransform(holes.astype(np.uint8), cv2.DIST_L2, 5)
        alpha = np.clip(rim / blend_pixels, 0, 1)[..., None]
        smooth = cv2.inpaint(atlas, holes.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
        blended = np.clip(output * alpha + smooth * (1 - alpha), 0, 255).astype(np.uint8)
        output[holes] = blended[holes]
    return output, {
        "patch_size": patch_size, "patches_copied": copies,
        "donors": "fully observed real atlas patches only; translation without rotation",
        "blend_pixels_inside_holes": blend_pixels,
    }
