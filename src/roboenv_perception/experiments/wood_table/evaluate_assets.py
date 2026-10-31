"""Reproject completed geometry into all video masks; separate fit and unused views."""

import argparse
from pathlib import Path

import cv2
import numpy as np

from roboenv_perception.construction.asset_fit import CalibratedViews, laptop_corners, rotation_z
from roboenv_perception.io import read_json, write_json


def read_obj(path):
    vertices, faces = [], []
    for line in path.read_text().splitlines():
        values = line.split()
        if values and values[0] == 'v':
            vertices.append(list(map(float, values[1:4])))
        elif values and values[0] == 'f':
            face = [int(v.split('/')[0])-1 for v in values[1:]]
            faces.extend([[face[0], face[i], face[i+1]] for i in range(1, len(face)-1)])
    return np.array(vertices), np.array(faces)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path('outputs/user-video-build-20261022'))
    args = parser.parse_args()
    views = CalibratedViews(args.source/'cameras-visible-tracks', read_json(args.root.parent/'user-video-isaac-20261023/input/geometry.json'))
    factor = read_json(args.root/'fit/scale-fit.json')['uniform_scale_correction']
    results, overlays = {}, []
    for asset in ['asset-0001', 'asset-0002', 'asset-0003']:
        spec = read_json(args.root/f'objects/{asset}/build-spec.json')
        if asset == 'asset-0002':
            vertices = laptop_corners(read_json(args.root/'fit/scale-fit.json')['parameters_provisional'])
            faces = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
            fitted = {1, 16}
        else:
            vertices, faces = read_obj(args.root/f'objects/{asset}/inputs/body.obj')
            pose = spec['pose_provisional']
            vertices = vertices@rotation_z(pose['yaw']).T/factor+pose['position']
            ev = read_json(args.source/f'retry-evidence/objects/{asset}/evidence.json')
            fitted = {int(v['frame_id'].split('-')[-1]) for v in ev['views']}
        rows = []
        for frame in range(41):
            index = read_json(args.source/f'perception/frames/frame-{frame:06}/objects/index.json')
            entry = next(o for o in index if o['object_id'].endswith('t'+asset[-4:]))
            mask = cv2.imread(str(args.source/'perception'/entry['mask_ref']), 0)
            shape = cv2.resize(mask, (480, 270), interpolation=cv2.INTER_NEAREST) > 0
            projection = np.rint(views.project(vertices, frame)/4).astype(np.int32)
            rendered = np.zeros(shape.shape, np.uint8)
            # Paint a union. Passing all overlapping projected triangles to
            # fillPoly at once can apply an even/odd fill rule and create holes.
            for face in faces:
                cv2.fillConvexPoly(rendered, projection[face], 1)
            rendered = rendered > 0
            iou = float((shape & rendered).sum()/max(1, (shape | rendered).sum()))
            rows.append({'frame': frame, 'used_for_object_fit': frame in fitted, 'silhouette_iou': iou})
            if frame == 16:
                image = cv2.imread(str(args.source/'perception/inputs/frames/000016.png'))
                image = cv2.resize(image, (480, 270))
                target = cv2.findContours(shape.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
                pred = cv2.findContours(rendered.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
                cv2.drawContours(image, target, -1, (0, 255, 0), 1)
                cv2.drawContours(image, pred, -1, (255, 0, 255), 1)
                cv2.putText(image, f'{spec["category"]}: IoU {iou:.3f}', (12, 24), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1)
                overlays.append(image)
        results[asset] = {'views': rows, 'mean_iou': float(np.mean([r['silhouette_iou'] for r in rows])),
                          'unused_view_mean_iou': float(np.mean([r['silhouette_iou'] for r in rows if not r['used_for_object_fit']]))}
    report = {'assets': results, 'scope': 'Mask agreement, not absolute dimensional accuracy. All views participated in SfM camera estimation.',
              'limitations': ['Masks are predictions; occlusions and specular transparency affect boundaries.',
                              'Laptop panel silhouettes omit thickness and stand; these are not contact accuracy metrics.']}
    write_json(args.root/'fit/geometry-validation.json', report)
    cv2.imwrite(str(args.root/'fit/silhouette-comparison.jpg'), np.concatenate(overlays))
    print({a: {k: v for k, v in r.items() if k != 'views'} for a, r in results.items()})


if __name__ == '__main__':
    main()
