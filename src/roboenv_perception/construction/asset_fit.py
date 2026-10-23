"""Calibrated, shared-scale object fitting. RGB priors never become measurements."""

import math
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import binary_closing, binary_fill_holes, label
from scipy.optimize import least_squares
from scipy.spatial import cKDTree

from ..io import read_json, write_json
from .reconstruction import intrinsics, read_colmap_text, world_to_camera


def rotation_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rotation_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def laptop_corners(parameters):
    x, y, z, yaw, tilt, opening, width, depth, lid = parameters
    # Looking toward the back edge from +Y, image-left is local +X.
    base = np.array([[width/2, 0, 0], [-width/2, 0, 0],
                     [-width/2, depth, 0], [width/2, depth, 0]])
    screen = np.array([[width/2, lid, 0], [-width/2, lid, 0],
                       [-width/2, 0, 0], [width/2, 0, 0]]) @ rotation_x(opening).T
    pose = rotation_z(yaw) @ rotation_x(tilt)
    return np.concatenate([base, screen]) @ pose.T + [x, y, z]


class CalibratedViews:
    def __init__(self, cameras_root: Path, provisional_geometry: dict):
        self.cameras, images, self.points = read_colmap_text(cameras_root/'colmap_original')
        self.images = {int(Path(im['name']).stem): im for im in images.values()}
        self.transform = np.array(provisional_geometry['sfm_to_sim'])
        self.inverse = np.linalg.inv(self.transform)

    def project(self, points, frame):
        source = np.asarray(points) @ self.inverse[:3, :3].T + self.inverse[:3, 3]
        image = self.images[frame]
        extrinsic = world_to_camera(image)
        camera = source @ extrinsic[:3, :3].T + extrinsic[:3, 3]
        k, distortion = intrinsics(self.cameras[image['camera_id']])
        # COLMAP SIMPLE_RADIAL uses k1*r^2, matching OpenCV's radial convention.
        pixels, _ = cv2.projectPoints(camera, np.zeros(3), np.zeros(3), k, distortion)
        return pixels[:, 0]

    def object_tracks(self, evidence_root, asset):
        evidence = read_json(evidence_root/f'objects/{asset}/evidence.json')
        counts = {}
        for view in evidence['views']:
            frame = int(view['frame_id'].split('-')[-1])
            mask = cv2.imread(str(evidence_root/view['mask_ref']), 0)
            mask = cv2.erode(mask, np.ones((9, 9), np.uint8))
            for x, y, point in self.images[frame]['points']:
                if point >= 0 and 0 <= int(x) < mask.shape[1] and 0 <= int(y) < mask.shape[0]:
                    if mask[int(y), int(x)]:
                        counts[point] = counts.get(point, 0)+1
        ids = [point for point, count in counts.items() if count >= 2]
        source = np.array([self.points[point]['xyz'] for point in ids])
        return source @ self.transform[:3, :3].T+self.transform[:3, 3], ids


def fit_laptop(views, annotations):
    observations = [(int(frame), np.array(corners)) for frame, corners in annotations.items()]

    def residual(p):
        image = np.concatenate([(views.project(laptop_corners(p), f)-q).ravel()
                                for f, q in observations])
        # Catalog aspect ratios constrain shape, not absolute scene scale.
        return np.r_[image, (p[7]/p[6]-0.2212/0.3126)*100,
                     (p[8]/p[6]-0.211/0.3126)*30]

    candidates = []
    for yaw in [0, math.pi/2, math.pi, -math.pi/2]:
        initial = [.62, -.09, .9, yaw, -.35, 1.9, .33, .233, .218]
        result = least_squares(residual, initial, max_nfev=1500, loss='soft_l1', f_scale=3,
                               bounds=([.35, -.3, .76, -7, -.9, 1.2, .22, .14, .13],
                                       [.85, .3, 1.02, 7, .1, 2.7, .5, .35, .32]))
        candidates.append(result)
    best = min(candidates, key=lambda r: np.mean(residual(r.x)**2))
    errors = {str(f): np.linalg.norm(views.project(laptop_corners(best.x), f)-q, axis=1).tolist()
              for f, q in observations}
    return {'parameters_provisional': best.x.tolist(), 'corner_errors_px': errors,
            'rms_corner_error_px': float(np.sqrt(np.mean(np.concatenate(list(errors.values()))**2))),
            'catalog_width_m': .3126, 'catalog_depth_m': .2212,
            'uniform_scale_correction': float(.3126/best.x[6]),
            'scale_source': 'estimated', 'model_identity': '14-inch MacBook Pro, confirmed by user; generation unknown',
            'scale_relative_uncertainty': .06,
            'scale_limitations': ['User confirmed size/family, not exact generation or metric measurements.',
                                  'All views participate in camera estimation; this is not independent metrology.'],
            'catalog_source': 'https://support.apple.com/en-hk/111902'}


def write_fit(root: Path, cameras: Path, evidence: Path, geometry: Path, annotations: Path):
    root.mkdir(parents=True, exist_ok=True)
    views = CalibratedViews(cameras, read_json(geometry))
    result = fit_laptop(views, read_json(annotations)['laptop'])
    write_json(root/'scale-fit.json', result)
    for asset in ['asset-0001', 'asset-0002', 'asset-0003']:
        points, ids = views.object_tracks(evidence, asset)
        np.savez_compressed(root/f'{asset}-tracks.npz', points=points, point_ids=ids)
    return result


def carve_visual_hull(views, evidence_root, asset, bounds, spacing=.0015):
    """Intersect calibrated silhouette cones; unobserved undersides stop at the table."""
    axes = [np.arange(lo, hi+spacing/2, spacing) for lo, hi in np.array(bounds).T]
    grid = np.stack(np.meshgrid(*axes, indexing='ij'), axis=-1)
    points = grid.reshape(-1, 3)
    inside = np.ones(len(points), dtype=bool)
    evidence = read_json(evidence_root/f'objects/{asset}/evidence.json')
    for view in evidence['views']:
        frame = int(view['frame_id'].split('-')[-1])
        mask = cv2.imread(str(evidence_root/view['mask_ref']), 0)
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
        pix = np.rint(views.project(points, frame)).astype(int)
        valid = (pix[:, 0] >= 0) & (pix[:, 0] < mask.shape[1])
        valid &= (pix[:, 1] >= 0) & (pix[:, 1] < mask.shape[0])
        observed = np.zeros(len(points), dtype=bool)
        observed[valid] = mask[pix[valid, 1], pix[valid, 0]] > 0
        inside &= observed
    occupied = inside.reshape(grid.shape[:3])
    occupied = binary_fill_holes(binary_closing(occupied, border_value=0))
    components, _ = label(occupied)
    sizes = np.bincount(components.ravel())
    sizes[0] = 0
    occupied = components == sizes.argmax()
    if occupied.sum() < 100:
        raise ValueError('silhouette intersection is empty or too small')
    return occupied, np.array([a[0] for a in axes]), spacing


def ring_mesh(sections, segments=48, exponent=2.8):
    """Closed rounded-section loft. Sections are (z, half_x, half_y, center_y)."""
    theta = np.arange(segments)*2*np.pi/segments
    circle = np.stack([np.sign(np.cos(theta))*abs(np.cos(theta))**(2/exponent),
                       np.sign(np.sin(theta))*abs(np.sin(theta))**(2/exponent)], axis=1)
    vertices = np.concatenate([np.c_[circle[:, 0]*rx, circle[:, 1]*ry+cy,
                                    np.full(segments, z)] for z, rx, ry, cy in sections])
    faces = []
    for ring in range(len(sections)-1):
        for i in range(segments):
            a, b = ring*segments+i, ring*segments+(i+1) % segments
            faces.extend([(a, b, b+segments), (a, b+segments, a+segments)])
    for i in range(1, segments-1):
        faces.extend([(0, i+1, i), ((len(sections)-1)*segments,
                      (len(sections)-1)*segments+i, (len(sections)-1)*segments+i+1)])
    return vertices, np.asarray(faces)


def bottle_mesh(width, depth, height):
    sections = [(0, .44*width, .44*depth, 0), (.025*height, .5*width, .5*depth, 0),
                (.62*height, .5*width, .5*depth, 0), (.74*height, .32*width, .32*depth, 0),
                (.78*height, .28*width, .28*depth, 0), (.92*height, .28*width, .28*depth, 0),
                (height, .28*width, .28*depth, 0)]
    return ring_mesh(sections, exponent=3.2)


def mouse_mesh(width, length, height):
    # A curved rigid body and a continuous, low thumb ledge; no invented joint.
    main = [(0, .39*width, .46*length, 0), (.07*height, .40*width, .49*length, 0),
            (.32*height, .41*width, .48*length, -.01*length),
            (.62*height, .40*width, .43*length, -.025*length),
            (.86*height, .32*width, .31*length, -.08*length),
            (height, .13*width, .12*length, -.13*length)]
    body, faces = ring_mesh(main, exponent=2.8)
    wing, wf = ring_mesh([(0, .23*width, .29*length, -.06*length),
                         (.12*height, .24*width, .30*length, -.06*length),
                         (.32*height, .14*width, .22*length, -.09*length),
                         (.40*height, .04*width, .12*length, -.11*length)], exponent=2.4)
    wing[:, 0] += .32*width
    return np.r_[body, wing], np.r_[faces, wf+len(body)]


def sample_polygon(contour, count=100):
    p = np.asarray(contour).reshape(-1, 2)
    lengths = np.linalg.norm(np.roll(p, -1, axis=0)-p, axis=1)
    distance = np.r_[0, np.cumsum(lengths)]
    targets = np.arange(count)*distance[-1]/count
    segment = np.clip(np.searchsorted(distance, targets, side='right')-1, 0, len(p)-1)
    ratio = (targets-distance[segment])/np.maximum(lengths[segment], 1e-9)
    return p[segment]+ratio[:, None]*(p[(segment+1) % len(p)]-p[segment])


def fit_rigid_silhouettes(views, evidence_root, asset, category):
    evidence = read_json(evidence_root/f'objects/{asset}/evidence.json')
    contours = []
    for view in evidence['views']:
        mask = cv2.imread(str(evidence_root/view['mask_ref']), 0)
        contour = max(cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        points = sample_polygon(contour)
        contours.append((int(view['frame_id'].split('-')[-1]), points, cKDTree(points)))
    if category == 'mouse':
        factory = mouse_mesh
        bounds = ([.25, -.02, -7, .06, .10, .04], [.36, .13, 7, .115, .18, .085])
        initial = [.30, .055, 0, .099, .147, .060]
    else:
        factory = bottle_mesh
        bounds = ([.86, .05, -7, .05, .05, .20], [.94, .14, 7, .11, .11, .29])
        initial = [.90, .10, 0, .078, .078, .247]

    def make(p):
        vertices, faces = factory(*p[3:])
        return vertices @ rotation_z(p[2]).T+[p[0], p[1], .75], faces

    def residual(p):
        vertices, _ = make(p)
        errors = []
        for frame, points, tree in contours:
            projection = views.project(vertices, frame).astype(np.float32)
            # Outer-envelope fitting is deliberately insensitive to button/wing seams.
            envelope = sample_polygon(cv2.convexHull(projection))
            errors.extend(tree.query(envelope)[0])
            errors.extend(cKDTree(envelope).query(points)[0])
        if category == 'mouse':
            errors.extend([(p[3]/p[4]-.0843/.1249)*70, (p[5]/p[4]-.051/.1249)*70])
        return errors

    candidates = []
    for yaw in [0, math.pi/2, math.pi, -math.pi/2]:
        initial[2] = yaw
        candidates.append(least_squares(residual, initial, bounds=bounds, max_nfev=250,
                                        diff_step=.001, loss='soft_l1', f_scale=3))
    best = min(candidates, key=lambda r: np.mean(np.asarray(residual(r.x))**2))
    vertices, faces = make(best.x)
    return {'category': category, 'parameters_provisional': best.x.tolist(),
            'mean_contour_distance_px': float(np.mean(residual(best.x))),
            'method': 'calibrated six-view outer-envelope fit with category shape prior',
            'limitations': ['Concavities and occluded undersides are completed from shape priors.',
                            'Mask boundaries and reflections introduce fitting bias.']}, vertices, faces
