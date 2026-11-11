import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from roboenv_perception.construction.video_relative_fit import fit_video_panels, panels


class Views:
    def __init__(self, scale):
        self.scale = scale
        self.cameras = {1: {'model': 'PINHOLE', 'params': [900, 900, 640, 360]}}
        self.images = {}
        self.extrinsics = {}
        for f, eye in [(1, [2, 3, 2]), (2, [-2, 4, 3]), (3, [1, 4, 1])]:
            eye = np.array(eye, dtype=float)
            forward = np.array([0, .2, .3])-eye
            forward /= np.linalg.norm(forward)
            right = np.cross(forward, [0, 0, 1])
            right /= np.linalg.norm(right)
            down = np.cross(forward, right)
            r = np.stack([right, down, forward])
            self.extrinsics[f] = np.c_[r, -r@eye*scale]
            # reconstruction.world_to_camera consumes scalar-first COLMAP quaternion.
            xyzw = Rotation.from_matrix(r).as_quat()
            self.images[f] = {'qvec': np.r_[xyzw[3], xyzw[:3]], 'tvec': -r@eye*scale, 'camera_id': 1}

    def project(self, points, frame):
        camera = points@self.extrinsics[frame][:3, :3].T+self.extrinsics[frame][:3, 3]
        return camera[:, :2]/camera[:, 2:]*900+[640, 360]


def test_video_fit_recovers_non_catalog_proportions_and_preserves_scale_ambiguity():
    results = []
    # Deliberately unlike any historical laptop dimensions/aspect ratios.
    for scale in [1, 7]:
        views = Views(scale)
        xyz = panels(1.4, .49, 1.12, 2.05)*scale
        annotations = {str(f): views.project(xyz, f).tolist() for f in [1, 2, 3]}
        fit = fit_video_panels(views, annotations)
        assert fit['absolute_scale_observable'] is False
        assert fit['depth'] == pytest.approx(.35, abs=1e-5)
        assert fit['lid_length'] == pytest.approx(.8, abs=1e-5)
        assert fit['opening_rad'] == pytest.approx(2.05, abs=1e-5)
        assert fit['rms_corner_error_px'] < 1e-5
        assert 'catalog_source' not in fit
        results.append(fit)
    assert results[1]['sfm_units_per_width'] == pytest.approx(7*results[0]['sfm_units_per_width'])


def test_video_policy_does_not_read_old_metric_or_product_files(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from roboenv_perception.components import reference_workflow, video_only_workflow
    from roboenv_perception.io import read_json, sha256, write_json

    archive = tmp_path/'user-scene-assets-20261023'
    source = tmp_path/'user-video-build-20261022'
    perception = tmp_path/'perception'
    video = perception/'inputs/source.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'controlled original video')
    monkeypatch.setattr(reference_workflow, 'VIDEO_SHA256', sha256(video))
    write_json(archive/'fit/annotations.json', {'laptop': {'1': [[0, 0]]*8, '16': [[1, 1]]*8}})
    for path in [archive/'fit/scale-fit.json', archive/'objects/asset-0002/blueprint.json',
                 tmp_path/'user-video-isaac-20261023/input/geometry.json']:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('INVALID: reading old product dimensions must fail')
    for path in [source/'perception/inputs/frames/000016.png',
                 source/'retry-evidence/objects/asset-0002/references.png',
                 source/'cameras-visible-tracks/colmap_original/images.txt']:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'video-only fixture')
    monkeypatch.setattr(video_only_workflow, 'raw_views', lambda path: object())
    monkeypatch.setattr(video_only_workflow, 'fit_video_panels', lambda views, annotations: {
        'depth': .44, 'lid_length': .81, 'opening_rad': 2.1, 'limitations': ['relative only'],
    })
    root = tmp_path/'new'
    receipt = video_only_workflow.prepare_fit({'perception': perception}, root,
                                            SimpleNamespace(reference_case_root=archive))
    spec = read_json(root/'objects/asset-0002/build-spec.json')
    assert spec['width_m'] == 1
    assert spec['depth_m'] == .44
    assert spec['absolute_scale_observable'] is False
    assert spec['scale_source'] == 'unknown'
    assert not any('metric' in key or 'blueprint' in key for key in receipt['pins'])
    assert 'catalog_source' not in read_json(root/'fit/relative-fit.json')


def test_exported_candidate_reprojection_accepts_implicit_zero_urdf_origins(tmp_path):
    from roboenv_perception.components.video_only_workflow import exported_mesh
    from roboenv_perception.io import write_json

    work = tmp_path/'objects/asset-0002/export'
    work.mkdir(parents=True)
    (work/'panel.obj').write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
    (work/'object.urdf').write_text('<robot><joint name="hinge"/><link name="lid"><visual>'
                                  '<geometry><mesh filename="panel.obj"/></geometry></visual></link></robot>')
    write_json(tmp_path/'fit/relative-fit.json', {'opening_rad': np.pi/2})
    [(vertices, faces)] = exported_mesh(tmp_path)
    np.testing.assert_allclose(vertices[2], [0, 0, 1], atol=1e-10)
    assert faces.tolist() == [[0, 1, 2]]


def test_video_texture_orientation_matches_rectified_physical_panel_corners():
    from roboenv_perception.construction.video_relative_render import panel_uv

    spec = {'depth_m': .7, 'lid_length_m': .68}
    # annotation order maps image corners: (right,top), (left,top),
    # (left,bottom), (right,bottom); Blender UV v=1 is image top.
    target = [(1, 1), (0, 1), (0, 0), (1, 0)]
    base = [( .5, 0), (-.5, 0), (-.5, .7), (.5, .7)]
    lid = [( .5, .68), (-.5, .68), (-.5, 0), (.5, 0)]
    assert [panel_uv('base', x, y, spec) for x, y in base] == target
    assert [panel_uv('lid', x, y, spec) for x, y in lid] == target
