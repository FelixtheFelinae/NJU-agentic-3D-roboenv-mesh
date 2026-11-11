"""Video-only rectangular panel fitting in raw SfM coordinates, up to similarity scale."""

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .asset_fit import CalibratedViews, rotation_x
from .reconstruction import intrinsics, world_to_camera


def panels(width, depth, lid, opening):
    base = np.array([[width/2, 0, 0], [-width/2, 0, 0],
                     [-width/2, depth, 0], [width/2, depth, 0]])
    screen = np.array([[width/2, lid, 0], [-width/2, lid, 0],
                       [-width/2, 0, 0], [width/2, 0, 0]]) @ rotation_x(opening).T
    return np.r_[base, screen]


def raw_views(cameras):
    # No sfm_to_sim, table size/height, product scale or fitted historical coordinates.
    return CalibratedViews(cameras, {'sfm_to_sim': np.eye(4).tolist()})


def triangulate(views, annotations):
    frames = list(annotations)
    rays, projections = [], []
    for frame in frames:
        image = views.images[int(frame)]
        k, distortion = intrinsics(views.cameras[image['camera_id']])
        rays.append(cv2.undistortPoints(np.float64(annotations[frame])[:, None], k, distortion)[:, 0])
        projections.append(world_to_camera(image)[:3])
    result = []
    for i in range(8):
        rows = []
        for ray, projection in zip(rays, projections):
            x, y = ray[i]
            rows.extend([x*projection[2]-projection[0], y*projection[2]-projection[1]])
        _, _, vh = np.linalg.svd(rows)
        result.append(vh[-1, :3]/vh[-1, 3])
    return np.array(result)


def fit_video_panels(views, annotations):
    if len(annotations) < 2:
        raise ValueError('Video-only fit needs at least two calibrated views')
    corners = triangulate(views, annotations)
    hinge = corners[[0, 1, 6, 7]].mean(axis=0)
    x = (corners[0]+corners[3]-corners[1]-corners[2])/2
    width = np.linalg.norm(x)
    x /= width
    y = corners[[2, 3]].mean(axis=0)-corners[[0, 1]].mean(axis=0)
    y -= x*np.dot(x, y)
    depth = np.linalg.norm(y)
    y /= depth
    z = np.cross(x, y)
    lid_vector = corners[[4, 5]].mean(axis=0)-corners[[6, 7]].mean(axis=0)
    opening = np.arctan2(np.dot(lid_vector, z), np.dot(lid_vector, y))
    if opening < 0:
        raise ValueError('Corner ordering yields a negative opening; inspect annotations')
    orientation = Rotation.from_matrix(np.stack([x, y, z], axis=1)).as_rotvec()
    initial = np.r_[np.zeros(3), orientation, opening, np.log([1, depth/width, np.linalg.norm(lid_vector)/width])]

    def world(p):
        local = panels(*np.exp(p[7:10]), p[6])
        return (local @ Rotation.from_rotvec(p[3:6]).as_matrix().T+p[:3])*width+hinge

    def residual(p):
        return np.concatenate([(views.project(world(p), int(f))-np.array(q)).ravel()
                               for f, q in annotations.items()])

    # Broad positive shape bounds are numerical guards, not SKU aspect ratios.
    low = np.r_[np.full(6, -np.inf), .01, np.full(3, np.log(.05))]
    high = np.r_[np.full(6, np.inf), np.pi-.01, np.full(3, np.log(20))]
    solved = least_squares(residual, initial, bounds=(low, high), loss='soft_l1',
                           f_scale=3, max_nfev=1200)
    if not solved.success or not np.isfinite(solved.x).all():
        raise ValueError('Video-only panel optimization did not converge')
    p = solved.x
    sizes = np.exp(p[7:10])
    rotation = Rotation.from_rotvec(p[3:6]).as_matrix()
    translation = p[:3]*width+hinge
    fitted_width = sizes[0]*width
    fitted_world = world(p)
    errors = {f: np.linalg.norm(views.project(fitted_world, int(f))-q, axis=1).tolist()
              for f, q in annotations.items()}
    for image in views.images.values():
        camera = world_to_camera(image)
        if image is views.images[int(next(iter(annotations)))]:
            if ((fitted_world @ camera[:3, :3].T+camera[:3, 3])[:, 2] <= 0).any():
                raise ValueError('Fitted panels lie behind the fitting camera')
    return {
        'evidence_policy': 'video_only', 'absolute_scale_observable': False,
        'units': 'relative, laptop width = 1; raw SfM scale is arbitrary',
        'width': 1.0, 'depth': float(sizes[1]/sizes[0]), 'lid_length': float(sizes[2]/sizes[0]),
        'opening_rad': float(p[6]), 'canonical_to_sfm_rotation': rotation.tolist(),
        'canonical_to_sfm_translation': translation.tolist(), 'sfm_units_per_width': float(fitted_width),
        'corner_errors_px': errors,
        'rms_corner_error_px': float(np.sqrt(np.mean(np.square(list(errors.values()))))),
        'fit_frames': list(annotations), 'optimizer_evaluations': solved.nfev,
        'method': 'raw calibrated multi-view pixel reprojection; two rectangular panels sharing a hinge and width; no catalog residuals',
        'limitations': ['Historical manual VIDEO pixel corners reused; not new automatic keypoints.',
                        'Rectangle/shared-width model is a geometric assumption.',
                        'No known-length reference, depth sensor or metric camera motion supplied.',
                        'Absolute dimensions, thickness, mass and hinge resistance are not recovered.',
                        'All camera views participate in SfM; held-out masks are not independent metrology.'],
    }


def project_relative(views, fit, points, frame):
    rotation = np.array(fit['canonical_to_sfm_rotation'])
    world = np.asarray(points) @ rotation.T*fit['sfm_units_per_width']
    world += fit['canonical_to_sfm_translation']
    return views.project(world, frame)
