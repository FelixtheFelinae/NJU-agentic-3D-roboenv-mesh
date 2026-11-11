"""Restricted same-video experiment without product dimensions or metric scene transforms."""

import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from ..construction.asset_agents import (
    AssetReview,
    child_environment,
    code_asset,
    compile_review_qa,
    run_artisan,
)
from ..construction.asset_fit import rotation_x
from ..construction.contracts import load_construction_config
from ..construction.design import infer_model
from ..construction.video_asset_workflow import read_obj
from ..construction.video_relative_fit import fit_video_panels, panels, project_relative, raw_views
from ..execution.context import emit
from ..io import read_json, relative_file, sha256, write_json
from ..vlm import make_vlm

POLICY = 'video_only'


def prepare_fit(paths, root, settings):
    from .reference_workflow import VIDEO_SHA256, _pin

    video = paths['perception']/'inputs/source.mp4'
    if not video.is_file() or sha256(video) != VIDEO_SHA256:
        raise ValueError('仅注册同一视频；不能迁移旧像素角点到其他输入')
    archive = settings.reference_case_root
    source = archive.parent/'user-video-build-20261022'
    # Read only pixel annotations and video-derived raw calibration. Deliberately
    # never open the old fit, blueprint, table transform or metric asset files.
    annotation_file = archive/'fit/annotations.json'
    annotations = read_json(annotation_file)['laptop']
    views = raw_views(source/'cameras-visible-tracks')
    fit = fit_video_panels(views, annotations)
    (root/'fit').mkdir(parents=True)
    write_json(root/'fit/annotations.json', {'laptop': annotations, 'source': 'historical manual video pixels only'})
    write_json(root/'fit/relative-fit.json', fit)
    depth, lid = fit['depth'], fit['lid_length']
    spec = {
        'asset_id': 'asset-0002', 'category': 'laptop', 'kind': 'articulated',
        'evidence_policy': POLICY, 'absolute_scale_observable': False,
        'coordinate_units': 'width-normalized illustration units, NOT recovered metres',
        # _m keys are a legacy SDK adapter vocabulary, explicitly not metrology.
        'width_m': 1.0, 'depth_m': depth, 'lid_length_m': lid,
        'base_thickness_m': .02, 'lid_thickness_m': .005,
        'thickness_source': 'UNOBSERVED generic illustration placeholders in width units, not product data or fitted dimensions',
        'parts': [{'name': 'base', 'est_mass_kg': 1.0}, {'name': 'lid', 'est_mass_kg': 1.0}],
        'mass_source': 'UNOBSERVED equal placeholder masses only to compile SDK; no dynamics acceptance',
        'joint': {'name': 'lid_hinge', 'type': 'revolute', 'axis': [1, 0, 0],
                  'origin': [0, 0, 0], 'lower': 0, 'upper': 3.0,
                  'range_source': 'illustration placeholder, not observed motion',
                  'motion_feel': {'effort': 'firm', 'motion': 'damped', 'hold': 'holds_anywhere'},
                  'physics_source': 'SDK placeholder; no friction, damping or holding claim'},
        'geometry_instructions': f'Use exactly base Box((1,{depth},.02)), Origin(xyz=(0,{depth/2},-.01)); '
            f'lid Box((1,{lid},.005)), Origin(xyz=(0,{lid/2},.0025)); '
            'q=0 closed, child frame is hinge frame; one passive +X joint at (0,0,0), limits [0,3]. '
            'Copy the declared MotionFeel intent only for SDK compatibility, not a tested property. '
            'No actuators, fixed root, branding, extra solids or internet-derived dimensions. '
            'All numbers are width-normalized illustration coordinates; NEVER introduce a metric product size.',
        'scene_opening_rad': fit['opening_rad'],
        'material': {'rgba': [.55, .55, .55, 1], 'material_class': 'anodized_metal'},
        'scale_source': 'unknown', 'evidence_refs': ['video-frame-000001', 'video-frame-000016'],
        'acceptance': ['Exactly two canonical rigid bodies and one passive hinge.',
                       'Dimensions match video-only width-normalized fit, not catalog priors.',
                       'Actual exported candidate must be compared against video masks.',
                       'Physical properties remain unverified; no absolute-size or all-angle-hold claim.'],
    }
    work = root/'objects/asset-0002'
    work.mkdir(parents=True)
    write_json(work/'build-spec.json', spec)
    # The evidence packet contains images only, excluding old semantic/model claims.
    packet = root/'video-evidence'
    packet.mkdir()
    image = source/'perception/inputs/frames/000016.png'
    shutil.copyfile(image, packet/'frame-000016.png')
    shutil.copyfile(source/'retry-evidence/objects/asset-0002/references.png', packet/'references.png')
    return {
        'case_id': 'laptop-video-only-20261109', 'evidence_policy': POLICY,
        'source_root': str(source.resolve()),
        'pins': {'video': _pin(video), 'video_pixels': _pin(annotation_file),
                 'raw_cameras': _pin(source/'cameras-visible-tracks/colmap_original'),
                 'video_frames_masks': _pin(source/'perception'),
                 'reference_images': _pin(source/'retry-evidence/objects/asset-0002/references.png')},
        'evidence_origin': 'Original video pixels, raw COLMAP cameras, video SAM masks and labelled historical manual pixel corners only. No product/catalog dimensions, old metric transforms or old models.',
        'known_limitations': fit['limitations'], 'history': [],
    }


def exported_mesh(root):
    work = root/'objects/asset-0002'
    fit = read_json(root/'fit/relative-fit.json')
    urdf = ET.parse(work/'export/object.urdf').getroot()
    joint = urdf.find('joint')
    joint_origin = joint.find('origin')
    joint_xyz = np.fromstring(joint_origin.get('xyz', '0 0 0') if joint_origin is not None else '0 0 0', sep=' ')
    result = []
    for link in urdf.findall('link'):
        for visual in link.findall('visual'):
            origin = visual.find('origin')
            xyz = np.fromstring(origin.get('xyz', '0 0 0') if origin is not None else '0 0 0', sep=' ')
            rpy = np.fromstring(origin.get('rpy', '0 0 0') if origin is not None else '0 0 0', sep=' ')
            mesh = visual.find('geometry/mesh')
            path = relative_file(work/'export', mesh.get('filename'))
            vertices, faces = read_obj(path)
            vertices *= np.fromstring(mesh.get('scale', '1 1 1'), sep=' ')
            vertices = vertices @ Rotation.from_euler('xyz', rpy).as_matrix().T+xyz
            if link.get('name') == 'lid':
                vertices = vertices @ rotation_x(fit['opening_rad']).T+joint_xyz
            result.append((vertices, faces))
    return result


def evaluate(root):
    receipt = read_json(root/'reference.json')
    source = Path(receipt['source_root'])
    views = raw_views(source/'cameras-visible-tracks')
    fit = read_json(root/'fit/relative-fit.json')
    envelope = panels(1, fit['depth'], fit['lid_length'], fit['opening_rad'])
    flat_faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    actual = exported_mesh(root)
    rows = []
    for frame in sorted(views.images):
        index_path = source/f'perception/frames/frame-{frame:06}/objects/index.json'
        if not index_path.exists():
            continue
        index = read_json(index_path)
        entry = next(o for o in index if o['object_id'].endswith('t0002'))
        mask = cv2.imread(str(relative_file(source/'perception', entry['mask_ref'])), 0)
        mask = cv2.resize(mask, (480, 270), interpolation=cv2.INTER_NEAREST) > 0
        row = {'frame': frame, 'used_for_panel_fit': str(frame) in fit['fit_frames']}
        for name, meshes in [('panel', [(envelope, flat_faces)]), ('actual_candidate', actual)]:
            rendered = np.zeros(mask.shape, np.uint8)
            for vertices, faces in meshes:
                projection = np.rint(project_relative(views, fit, vertices, frame)/4).astype(np.int32)
                for face in faces:
                    cv2.fillConvexPoly(rendered, projection[face], 1)
            shape = rendered > 0
            row[name+'_iou'] = float((mask & shape).sum()/max(1, (mask | shape).sum()))
            if frame == 16 and name == 'actual_candidate':
                image = cv2.resize(cv2.imread(str(source/'perception/inputs/frames/000016.png')), (480, 270))
                for shape_, color in [(mask, (0, 255, 0)), (shape, (255, 0, 255))]:
                    contours = cv2.findContours(shape_.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
                    cv2.drawContours(image, contours, -1, color, 1)
                cv2.imwrite(str(root/'fit/silhouette-comparison.png'), image)
        rows.append(row)
    report = {'views': rows, 'view_count': len(rows),
              'panel_mean_iou': float(np.mean([r['panel_iou'] for r in rows])),
              'actual_candidate_mean_iou': float(np.mean([r['actual_candidate_iou'] for r in rows])),
              'unused_view_candidate_mean_iou': float(np.mean([r['actual_candidate_iou'] for r in rows if not r['used_for_panel_fit']])),
              'candidate_sha256': sha256(root/'objects/asset-0002/model.py'),
              'export_hashes': export_hashes(root),
              'scope': 'Video-mask consistency of fitted panels AND actual exported visual meshes; not metric accuracy, all cameras use video views.'}
    # Explicitly demonstrate the projective scale ambiguity with a 2x scene/camera test.
    camera = world_to_camera_for_scale(views, 16)
    xyz = envelope @ np.array(fit['canonical_to_sfm_rotation']).T*fit['sfm_units_per_width']+fit['canonical_to_sfm_translation']
    p = xyz @ camera[:3, :3].T+camera[:3, 3]
    doubled = (2*xyz) @ camera[:3, :3].T+2*camera[:3, 3]
    report['double_scene_and_camera_scale_pixel_normalized_max_error'] = float(np.max(abs(p[:, :2]/p[:, 2:]-doubled[:, :2]/doubled[:, 2:])))
    write_json(root/'fit/geometry-validation.json', report)
    if report['panel_mean_iou'] < .94 or report['actual_candidate_mean_iou'] < .94:
        raise ValueError('Video-only fitted panel or actual candidate silhouette below unchanged 0.94 reference threshold')
    return report


def world_to_camera_for_scale(views, frame):
    from ..construction.reconstruction import world_to_camera
    return world_to_camera(views.images[frame])


def export_hashes(root):
    from ..artifacts.registry import tree_index
    return tree_index(root/'objects/asset-0002/export')


def snapshot_summary(root, receipt):
    work = root/'objects/asset-0002'
    review = read_json(work/'review.json') if (work/'review.json').exists() else None
    from .reference_workflow import _review_summary
    return {
        **{k: receipt[k] for k in ('case_id', 'evidence_policy', 'evidence_origin', 'known_limitations', 'history')},
        'latest': receipt['history'][-1], 'relative_fit': read_json(root/'fit/relative-fit.json'),
        'geometry': read_json(root/'fit/geometry-validation.json') if (root/'fit/geometry-validation.json').exists() else None,
        'assets': {'asset-0002': {'build_spec': read_json(work/'build-spec.json'),
                                  'compiled': bool(read_json(work/'build.json').get('passed')) if (work/'build.json').exists() else False,
                                  'qa': read_json(work/'qa.json') if (work/'qa.json').exists() else None,
                                  'review': review['decision'] if review else None,
                                  'review_details': _review_summary(review) if review else None}},
        'physical_validation': 'not_run: absolute size/mass/friction unknown; normalized visual/geometry experiment only',
        'available_images': [str(p.relative_to(root)) for p in (root/'renders').glob('*.png')],
    }


def execute(operation, paths, output, settings, params, feedback):
    from .reference_workflow import verify_pins
    root = output/'reference'
    record = {'operation': operation, 'params': params, 'started_unix': time.time()}
    if operation == 'reference_fit':
        receipt = prepare_fit(paths, root, settings)
    else:
        receipt = read_json(paths['reference']/'reference.json')
        verify_pins(receipt['pins'])
        shutil.copytree(paths['reference'], root, ignore=shutil.ignore_patterns('__pycache__', 'kit-runtime'))
        write_json(root/'reference.json', receipt)
        work = root/'objects/asset-0002'
        if params.get('asset_id', 'asset-0002') != 'asset-0002':
            raise ValueError('Video-only experiment only builds the laptop asset-0002')
        spec = read_json(work/'build-spec.json')
        docs = settings.artisan_python.parents[3]/'sdk/_docs'
        try:
            if operation == 'reference_code':
                for path in [root/'fit/geometry-validation.json', work/'review.json', work/'qa.json']:
                    path.unlink(missing_ok=True)
                shutil.rmtree(root/'renders', ignore_errors=True)
                config = load_construction_config(settings.construction_config).vlm
                code_asset(config, spec, [root/'video-evidence/references.png'], work, settings.artisan_python,
                           (docs/'common/30_articulated_object.md').read_text()[:7000]+(docs/'common/20_core_types.md').read_text()[:13000],
                           max_revisions=2, review_feedback='\n'.join(feedback))
            elif operation == 'reference_validate':
                stage = params.get('stage')
                if stage == 'appearance':
                    annotations = read_json(root/'fit/annotations.json')['laptop']['16']
                    image = cv2.imread(str(root/'video-evidence/frame-000016.png'))
                    dest = work/'appearance'
                    dest.mkdir(exist_ok=True)
                    for name, corners, height in [('keyboard', annotations[:4], 720), ('screen', annotations[4:], 700)]:
                        target = np.float32([[1023, 0], [0, 0], [0, height-1], [1023, height-1]])
                        transform = cv2.getPerspectiveTransform(np.float32(corners), target)
                        cv2.imwrite(str(dest/f'{name}.png'), cv2.warpPerspective(image, transform, (1024, height)))
                    write_json(dest/'provenance.json', {'source': 'original video frame 16', 'policy': POLICY,
                               'operation': 'manual video pixel corners, perspective rectification; no web or 3DGS textures'})
                    shutil.rmtree(root/'renders', ignore_errors=True)
                    (work/'review.json').unlink(missing_ok=True)
                elif stage == 'geometry':
                    record['metrics'] = {k: v for k, v in evaluate(root).items() if k not in ('views', 'export_hashes')}
                elif stage == 'qa':
                    (work/'qa_tests.py').write_text(compile_review_qa(['topology', 'hinge', 'clearance'], spec))
                    if not run_artisan(settings.artisan_python, work, mode='qa')['passed']:
                        raise ValueError('Current normalized candidate QA failed')
                elif stage == 'render':
                    if not settings.blender:
                        raise ValueError('Blender runtime missing')
                    shutil.rmtree(root/'renders', ignore_errors=True)
                    (work/'review.json').unlink(missing_ok=True)
                    worker = Path(__file__).parents[1]/'construction/video_relative_render.py'
                    with (root/'render.log').open('w') as log:
                        subprocess.run([str(settings.blender), '-b', '--python-exit-code', '1', '--python', str(worker), '--', str(root.resolve())],
                                       stdout=log, stderr=subprocess.STDOUT, env=child_environment(), check=True,
                                       timeout=min(settings.max_wall_seconds-10, 600))
                    render_report = read_json(root/'renders/result.json')
                    if not render_report.get('previews') or any(not (root/'renders'/p).is_file() for p in render_report['previews']):
                        raise ValueError('Renderer exited without actual required preview files')
                else:
                    raise ValueError('Video-only stages: appearance, geometry, qa, render; metric physics deliberately unavailable')
            elif operation == 'reference_review':
                geometry = read_json(root/'fit/geometry-validation.json')
                if geometry['candidate_sha256'] != sha256(work/'model.py') or geometry['export_hashes'] != export_hashes(root):
                    raise ValueError('Actual candidate changed after silhouette evaluation')
                qa = read_json(work/'qa.json')
                if not qa['passed']:
                    raise ValueError('Candidate QA has not passed')
                render = read_json(root/'renders/result.json')
                if render['candidate_sha256'] != sha256(work/'model.py') or render['export_hashes'] != export_hashes(root):
                    raise ValueError('Candidate render is stale')
                config = load_construction_config(settings.construction_config).vlm
                vlm = make_vlm(config, system='Independent VIDEO-ONLY visual/geometry reviewer. JSON only. '
                    'No external product dimensions, brand identification, catalog claims or physical success. '
                    'Use only supplied video images, raw camera fit, exact candidate render and QA. '
                    'Absolute metric size, thickness, masses and hinge behavior are unobserved. '
                    'Placeholder SDK units/masses/feel are not recovered measurements. '
                    'PASS is limited to video appearance/proportions/canonical geometry; no dynamics or holding acceptance. '
                    'Explicitly compare photograph orientation: screen top/notch must be away from hinge, '
                    'screen text upright, keyboard near hinge and touchpad toward the front. '
                    'Do not describe visible projected photographic textures as absent. '
                    'Return exact candidate_sha256, decision PASS or REDO, issues, observations, qa_plan topology/hinge/clearance. '
                    'Include scale ambiguity and unobserved physical properties in observations.')
                context = {'candidate_sha256': sha256(work/'model.py'), 'blueprint': spec,
                           'video_fit': read_json(root/'fit/relative-fit.json'), 'geometry': {k: v for k, v in geometry.items() if k not in ('views', 'export_hashes')},
                           'qa': qa, 'source': (work/'model.py').read_text()}
                import json
                review = infer_model(vlm, 'video-only-review', json.dumps(context),
                                     [root/'video-evidence/references.png', root/'renders/preview.png', root/'fit/silhouette-comparison.png'],
                                     AssetReview, work/'traces/video-only-review')
                if review.candidate_sha256 != sha256(work/'model.py'):
                    raise ValueError('Reviewer did not bind current candidate')
                report = review.model_dump(mode='json')
                report.update(scope='video-only appearance/proportion/canonical geometry; physics NOT validated',
                              export_hashes=export_hashes(root), geometry_sha256=sha256(root/'fit/geometry-validation.json'),
                              render_report_sha256=sha256(root/'renders/result.json'),
                              render_image_hashes={p: sha256(root/'renders'/p) for p in render['previews']})
                write_json(work/'review.json', report)
                record['decision'] = report['decision']
                if report['decision'] != 'PASS':
                    record['status'] = 'unverified'
        except (ValueError, RuntimeError, FileNotFoundError, subprocess.SubprocessError) as error:
            record.update(status='unverified', error=f'{type(error).__name__}: {error}'[:12000])
    verify_pins(receipt['pins'])
    record.setdefault('status', 'completed')
    record['ended_unix'] = time.time()
    receipt['history'].append(record)
    write_json(root/'reference.json', receipt)
    write_json(root/'observation.json', snapshot_summary(root, receipt))
    emit('reference.video_only_stage', **record)
    return {'reference': 'reference'}, 'unverified'
