import math

import numpy as np
import pytest

from roboenv_perception.construction.asset_fit import (
    bottle_mesh,
    fit_laptop,
    laptop_corners,
    rotation_x,
    rotation_z,
)


def test_bottle_loft_is_closed_outward_and_metric():
    vertices, faces = bottle_mesh(.062, .060, .205)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    assert (counts == 2).all()
    volume = np.einsum('ij,ij->i', vertices[faces[:, 0]],
                       np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]])).sum()/6
    assert 0 < volume < .062*.060*.205
    np.testing.assert_allclose(np.ptp(vertices, axis=0), [.062, .060, .205])


def test_laptop_rest_convention_and_local_hinge_direction():
    p = [0, 0, 0, 0, 0, 0, .3126, .2212, .216]
    closed = laptop_corners(p)
    assert np.allclose(closed[:, 2], 0)
    p[5] = math.pi/2
    opened = laptop_corners(p)
    assert opened[4, 2] == pytest.approx(.216)
    np.testing.assert_allclose(opened[:4], closed[:4])
    assert np.linalg.det(rotation_z(.3)@rotation_x(-.4)) == pytest.approx(1)


def test_two_view_fit_recovers_pose_and_one_shared_catalog_scale():
    class Views:
        def project(self, points, frame):
            eye = np.array([1.6, 1.4, 1.5]) if frame == 1 else np.array([.1, 1.6, 1.2])
            forward = np.array([.61, 0, .9])-eye
            forward /= np.linalg.norm(forward)
            right = np.cross(forward, [0, 0, 1])
            right /= np.linalg.norm(right)
            down = np.cross(forward, right)
            camera = (points-eye)@np.stack([right, down, forward]).T
            return camera[:, :2]/camera[:, 2:]*1650+[960, 540]

    width = .357
    p = [.61, -.05, .87, .02, -.39, 2.24, width, width*.2212/.3126, width*.211/.3126]
    views = Views()
    result = fit_laptop(views, {str(i): views.project(laptop_corners(p), i).tolist() for i in [1, 16]})
    assert result['rms_corner_error_px'] < .01
    assert result['uniform_scale_correction'] == pytest.approx(.3126/width, rel=1e-3)
    assert result['scale_source'] == 'estimated'
