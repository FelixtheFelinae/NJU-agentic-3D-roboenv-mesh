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
    write_fit,
)
from roboenv_perception.construction.contracts import load_construction_config
from roboenv_perception.construction.video_asset_workflow import appearance, prepare
from roboenv_perception.io import read_json, sha256, write_json
from roboenv_perception.secret_env import load_secret_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fit', 'prepare', 'appearance', 'code', 'review', 'scene-review'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, default=Path('outputs/user-video-build-20261022/retry-evidence'))
    parser.add_argument('--config', type=Path, default=Path('configs/local-user-design-retry.toml'))
    parser.add_argument('--env-file', type=Path, default=Path('.env.local'))
    parser.add_argument('--artisan', type=Path, help='Artisan checkout; required for prepare/code/review')
    parser.add_argument('--asset', default='asset-0003')
    args = parser.parse_args()
    if args.mode in {'prepare', 'code', 'review'} and args.artisan is None:
        parser.error('--artisan is required for prepare/code/review; pass your Artisan checkout')
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
