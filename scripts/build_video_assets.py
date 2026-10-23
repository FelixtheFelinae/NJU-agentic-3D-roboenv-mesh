"""Reproducible video asset Coding/Review experiment; three original objects only."""

import argparse
import json
from pathlib import Path

import numpy as np

from roboenv_perception.construction.asset_agents import (
    code_asset,
    review_asset,
    review_scene,
    run_artisan,
)
from roboenv_perception.construction.asset_fit import (
    CalibratedViews,
    fit_rigid_silhouettes,
    rotation_z,
    write_fit,
)
from roboenv_perception.construction.contracts import load_construction_config
from roboenv_perception.construction.reconstruction import intrinsics, world_to_camera
from roboenv_perception.io import read_json, sha256, write_json
from roboenv_perception.secret_env import load_secret_env


def save_obj(path, vertices, faces):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as f:
        for v in vertices:
            f.write('v '+' '.join(f'{x:.9f}' for x in v)+'\n')
        for face in faces:
            f.write('f '+' '.join(str(int(i)+1) for i in face)+'\n')


def prepare(root, evidence):
    fit = read_json(root/'fit/scale-fit.json')
    factor = fit['uniform_scale_correction']
    for asset, category, mass in [('asset-0003', 'mouse', .141), ('asset-0001', 'bottle', .060)]:
        work = root/'objects'/asset
        work.mkdir(parents=True, exist_ok=True)
        result = read_json(root/f'fit/{asset}-fit.json')
        p = result['parameters_provisional']
        mesh = np.load(root/f'fit/{asset}-mesh.npz')
        vertices = (mesh['vertices']-[p[0], p[1], .75]) @ rotation_z(p[2])*factor
        save_obj(work/'inputs/body.obj', vertices, mesh['faces'])
        spec = {'asset_id': asset, 'category': category, 'kind': 'rigid',
                'parts': [{'name': 'body', 'mesh': 'inputs/body.obj', 'est_mass_kg': mass}],
                'dimensions_m': (np.ptp(vertices, axis=0)).tolist(),
                'pose_provisional': {'position': [p[0], p[1], .75], 'yaw': p[2]},
                'geometry_source': result, 'scale_fit_sha256': sha256(root/'fit/scale-fit.json'),
                'scale_source': 'estimated', 'scale_relative_uncertainty': .06,
                'mass_source': 'catalog family prior' if category == 'mouse' else 'assumed empty capped PET bottle; fill state unknown',
                'mass_limitations': 'No mass or fill state was measured from the video.',
                'material': {'rgba': [.79, .80, .76, 1] if category == 'mouse' else [.70, .79, .73, 1],
                             'material_class': 'plastic'},
                'interactions': ['pick up', 'push', 'tip', 'rest on table'],
                'mechanism_constraints': 'Exactly one rigid body. Fixed thumb ledge/cap/label. No invented joints.',
                'acceptance': ['Metric closed mesh with positive volume and inertia.',
                               'Exactly one rigid body and no moving joints.',
                               'Actual exported USD settles, translates under force, and remains finite.'],
                'evidence_refs': ['view-001', 'view-002', 'view-003', 'view-004', 'view-005', 'view-006']}
        if category == 'mouse':
            spec['catalog_reference'] = 'https://www.logitech.com/content/dam/logitech/en/business/pdf/mx-master-3s-business-wireless-mouse.pdf'
            spec['catalog_limitations'] = 'Model family is a hypothesis. Fitted dimensions are retained; exact SKU is not confirmed.'
        write_json(work/'build-spec.json', spec)

    work = root/'objects/asset-0002'
    work.mkdir(parents=True, exist_ok=True)
    p = fit['parameters_provisional']
    width, depth, lid = np.array(p[6:])*factor
    spec = {'asset_id': 'asset-0002', 'category': 'laptop', 'kind': 'articulated',
            'width_m': width, 'depth_m': depth, 'lid_length_m': lid,
            'base_thickness_m': .0112, 'lid_thickness_m': .0033,
            'parts': [{'name': 'base', 'est_mass_kg': 1.15}, {'name': 'lid', 'est_mass_kg': .45}],
            'joint': {'name': 'lid_hinge', 'type': 'revolute', 'axis': [1, 0, 0],
                      'origin': [0, 0, .001], 'lower': 0, 'upper': 2.40,
                      'range_source': 'assumed conservative working range, not observed motion',
                      'motion_feel': {'effort': 'firm', 'motion': 'damped', 'hold': 'holds_anywhere'}},
            'geometry_instructions': 'Base Box((width,depth,.0112)), Origin(xyz=(0,depth/2,-.0056)). '
                                     'Lid Box((width,lid_length,.0033)), Origin(xyz=(0,lid_length/2,.00165)). '
                                     'Canonical q=0 is closed. Child frame is the hinge frame. '
                                     'One revolute joint at (0,0,.001), axis +X, lower=0 upper=2.40. '
                                     'Use MotionFeel.from_intent(effort="firm", motion="damped", hold="holds_anywhere"). '
                                     'No fixed root or actuators; stand is a separate scene fixture.',
            'scene_opening_rad': p[5], 'scene_base_tilt_rad': p[4],
            'pose_provisional': {'position': p[:3], 'yaw': p[3]},
            'scale_source': 'estimated', 'scale_relative_uncertainty': .06,
            'scale_fit_sha256': sha256(root/'fit/scale-fit.json'),
            'material': {'rgba': [.72, .71, .64, 1], 'material_class': 'anodized_metal'},
            'mass_source': '1.60 kg catalog prior for 14-inch MBP; generation unknown; base/lid split assumed',
            'acceptance': ['Exactly two movable rigid links and one passive revolute hinge.',
                           'q=0 is closed; no interpenetration over allowed opening range.',
                           'Actual USD settles on separate stand without welded attachment.',
                           'Hinge holds its scene angle under gravity and moves under external torque.'],
            'evidence_refs': ['view-001', 'view-002', 'view-003', 'view-004', 'view-005', 'view-006']}
    write_json(work/'build-spec.json', spec)
    write_json(root/'scene-build-spec.json', {'objects': ['asset-0001', 'asset-0002', 'asset-0003'],
               'fixtures': ['table', 'laptop_stand'], 'robots': [], 'test_cubes': [],
               'table_width_m': .8*factor, 'top_height_m': .75,
               'height_source': 'task_design for G1 operation, not estimated floor distance',
               'scale_source': 'estimated', 'scale_fit': fit})


def appearance(root, evidence):
    import cv2

    annotations = read_json(root/'fit/annotations.json')['laptop']['16']
    frame = cv2.imread(str(evidence/'objects/asset-0002/views/view-004/frame.png'))
    work = root/'objects/asset-0002/appearance'
    work.mkdir(exist_ok=True)
    for name, corners, height in [('keyboard', annotations[:4], 720), ('screen', annotations[4:], 700)]:
        target = np.float32([[1023, 0], [0, 0], [0, height-1], [1023, height-1]])
        homography = cv2.getPerspectiveTransform(np.float32(corners), target)
        cv2.imwrite(str(work/f'{name}.png'), cv2.warpPerspective(frame, homography, (1024, height)))
    write_json(work/'provenance.json', {'source': 'original frame-000016',
               'operation': 'perspective rectification of manually annotated physical panel corners',
               'scope': 'visual-only photo projection; screen content is a fixed snapshot'})
    work = root/'objects/asset-0001/appearance'
    work.mkdir(exist_ok=True)
    source = np.float32([[327, 681], [427, 656], [462, 829], [363, 850]])
    target = np.float32([[0, 0], [255, 0], [255, 511], [0, 511]])
    cv2.imwrite(str(work/'label.png'), cv2.warpPerspective(frame, cv2.getPerspectiveTransform(source, target), (256, 512)))
    write_json(work/'provenance.json', {'source': 'original frame-000016',
               'operation': 'approximate manual front-label quadrilateral rectification',
               'corners_px': source.tolist(), 'scope': 'appearance only; back label reuses front label'})
    work = root/'objects/asset-0003/appearance'
    work.mkdir(exist_ok=True)
    ev = read_json(evidence/'objects/asset-0003/evidence.json')['views'][-1]
    image = cv2.imread(str(evidence/ev['frame_ref']))
    mask = cv2.imread(str(evidence/ev['mask_ref']), 0)
    reliable = cv2.erode(mask, np.ones((5, 5), np.uint8)) > 0
    color = np.median(image[reliable], axis=0).astype(np.uint8)
    image[~reliable] = color
    box = ev['context_box_xyxy']
    cv2.imwrite(str(work/'mouse.png'), image[box[1]:box[3], box[0]:box[2]])
    views = CalibratedViews(evidence.parent/'cameras-visible-tracks', read_json(root.parent/'user-video-isaac-20261023/input/geometry.json'))
    frame_id = int(ev['frame_id'].split('-')[-1])
    im = views.images[frame_id]
    spec = read_json(work.parent/'build-spec.json')
    p = spec['pose_provisional']
    factor = read_json(root/'fit/scale-fit.json')['uniform_scale_correction']
    pose = np.eye(4)
    pose[:3, :3] = rotation_z(p['yaw'])/factor
    pose[:3, 3] = p['position']
    local_to_camera = world_to_camera(im) @ views.inverse @ pose
    k, distortion = intrinsics(views.cameras[im['camera_id']])
    write_json(work/'projection.json', {'local_to_camera': local_to_camera.tolist(),
               'intrinsics': k.tolist(), 'radial_k1': float(distortion[0]), 'crop_box': box,
               'source_frame': ev['frame_id'], 'scope': 'single-view projected appearance; hidden faces share projected colors'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fit', 'prepare', 'appearance', 'code', 'review', 'scene-review'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, default=Path('outputs/user-video-build-20261022/retry-evidence'))
    parser.add_argument('--config', type=Path, default=Path('configs/local-user-design-retry.toml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env.local'))
    parser.add_argument('--artisan', type=Path, default=Path('/home/felixz/real2sim/R2S2B/sources/Artisan'))
    parser.add_argument('--asset', default='asset-0003')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == 'fit':
        source = args.evidence.resolve().parent
        geometry = root.parent/'user-video-isaac-20261023/input/geometry.json'
        write_fit(root/'fit', source/'cameras-visible-tracks', args.evidence.resolve(), geometry,
                  root/'fit/annotations.json')
        views = CalibratedViews(source/'cameras-visible-tracks', read_json(geometry))
        for asset, category in [('asset-0001', 'bottle'), ('asset-0003', 'mouse')]:
            result, vertices, faces = fit_rigid_silhouettes(views, args.evidence.resolve(), asset, category)
            write_json(root/f'fit/{asset}-fit.json', result)
            np.savez_compressed(root/f'fit/{asset}-mesh.npz', vertices=vertices, faces=faces)
        return
    if args.mode == 'prepare':
        prepare(root, args.evidence)
        for asset in ['asset-0001', 'asset-0003']:
            run_artisan(args.artisan/'.artisan/venv/bin/python', root/'objects'/asset, mode='union')
        return
    if args.mode == 'appearance':
        appearance(root, args.evidence.resolve())
        return
    load_secret_env(args.env_file)
    config = load_construction_config(args.config).vlm
    if args.mode == 'scene-review':
        images = [args.evidence.resolve()/'objects/asset-0002/views/view-001/frame.png',
                  args.evidence.resolve()/'objects/asset-0002/views/view-004/frame.png',
                  root/'simulation/frame-initial.png', root/'simulation/frame-final.png']
        result = review_scene(config, root, images)
        print(json.dumps({'scene_review': result['decision']}))
        return
    work = root/'objects'/args.asset
    board = args.evidence.resolve()/f'objects/{args.asset}/references.png'
    python = args.artisan/'.artisan/venv/bin/python'
    docs = args.artisan/'sdk/_docs'
    if args.mode == 'code':
        reference = (docs/'common/30_articulated_object.md').read_text()[:7000]
        reference += (docs/'common/20_core_types.md').read_text()[:13000]
        code_asset(config, read_json(work/'build-spec.json'), [board], work, python, reference)
        print(json.dumps({'asset': args.asset, 'coding': 'compiled_and_exported'}))
    else:
        report = root/'simulation/validation.json'
        physics = read_json(report)
        physics['report_sha256'] = sha256(report)
        reload_report = read_json(root/'scene-reload/validation.json')
        if reload_report['status'] != 'passed' or not all(reload_report['checks'].values()):
            raise RuntimeError('Portable scene reload has not passed')
        if physics.get('scene_sha256') != sha256(root/'simulation/scene.usda') or reload_report.get('scene_sha256') != physics['scene_sha256']:
            raise RuntimeError('Scene changed after physics/reload verification')
        physics['scene_reload'] = reload_report
        physics['scene_reload_sha256'] = sha256(root/'scene-reload/validation.json')
        geometry_report = read_json(root/'fit/geometry-validation.json')
        physics['geometry_validation'] = {
            'metrics': {k: v for k, v in geometry_report['assets'][args.asset].items() if k != 'views'},
            'limitations': geometry_report['limitations'], 'scope': geometry_report['scope']}
        image = root/'simulation/frame-initial.png'
        closeup = root/f'simulation/asset-{read_json(work/"build-spec.json")["category"]}.png'
        images = [board, closeup, image] if closeup.exists() else [board, image]
        result = review_asset(config, work, images, physics, python,
                              (docs/'common/80_testing.md').read_text()[:15000])
        print(json.dumps({'asset': args.asset, 'review': result['decision']}))


if __name__ == '__main__':
    main()
