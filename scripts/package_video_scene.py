"""Package only reviewed scene artifacts; reject changes after physics or Review."""

import argparse
import json
import zipfile
from pathlib import Path

from roboenv_perception.io import read_json, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    scene_review = read_json(root/'scene-review.json')
    physics = read_json(root/'simulation/validation.json')
    reload = read_json(root/'scene-reload/validation.json')
    scene_hash = sha256(root/'simulation/scene.usda')
    if scene_review['decision'] != 'PASS' or any(r['scene_sha256'] != scene_hash for r in [scene_review, physics, reload]):
        raise ValueError('Scene has not passed Review or changed after verification')
    for file, key in [('simulation/validation.json', 'physics_report_sha256'),
                      ('scene-reload/validation.json', 'reload_report_sha256')]:
        if sha256(root/file) != scene_review[key]:
            raise ValueError('Scene evidence changed: '+file)
    for report in [physics, reload]:
        if report['status'] != 'passed' or not report['checks'] or not all(report['checks'].values()):
            raise ValueError('Physics checks have not all passed')
    for asset, digest in scene_review['object_review_hashes'].items():
        work = root/'objects'/asset
        if sha256(work/'review.json') != digest:
            raise ValueError('Object Review changed: '+asset)
        review = read_json(work/'review.json')
        if review['decision'] != 'PASS' or not review['qa']['passed']:
            raise ValueError('Object did not pass: '+asset)
        for filename, key in [('model.py', 'candidate_sha256'), ('qa_tests.py', 'qa_sha256'), ('blueprint.json', 'blueprint_sha256')]:
            if sha256(work/filename) != review[key]:
                raise ValueError('Reviewed source changed: '+asset+'/'+filename)
        for ref, expected in review['input_hashes'].items():
            if sha256(work/ref) != expected:
                raise ValueError('Reviewed input changed: '+ref)
    for binding in physics['candidate_sources'].values():
        base = (root/'simulation'/binding['usd_ref']).parent
        for ref, expected in binding['dependency_hashes'].items():
            if sha256(base/ref) != expected:
                raise ValueError('Tested USD/dependency changed: '+str(base/ref))

    # Confirm the final geometry report is exactly what the scene reviewer received.
    request = read_json(root/'traces/scene-review/request.json')
    reviewed_context, _ = json.JSONDecoder().raw_decode(request['prompt'])
    if reviewed_context['geometry'] != read_json(root/'fit/geometry-validation.json'):
        raise ValueError('Geometry report differs from scene Review input')
    geometry = read_json(root/'fit/geometry-validation.json')
    summary = {'status': 'reviewed_interactive_scene_prototype', 'scene_ref': 'simulation/scene.usda',
               'scene_review': 'PASS', 'physics_checks_passed': len(physics['checks']),
               'fresh_reload_checks_passed': len(reload['checks']),
               'objects': ['bottle', '14-inch MacBook Pro', 'mouse'],
               'fixtures': ['table', 'separate static laptop stand'], 'robots': [], 'test_blocks': [],
               'scale_source': 'estimated from user-confirmed 14-inch MBP catalog width + calibrated views',
               'table_size_m': [physics['table_geometry']['width_m'], physics['table_geometry']['depth_m']],
               'table_height_m': .75, 'height_source': 'task_design',
               'silhouette_mean_iou': {a: r['mean_iou'] for a, r in geometry['assets'].items()},
               'real_world_accuracy_validated': False, 'grasp_controller_validated': False,
               'limitations': ['Mouse geometry remains approximate; exact mouse SKU not confirmed.',
                               'Mass, friction, restitution, hinge resistance and hidden supports contain assumptions.',
                               'Bottle is a capped rigid exterior proxy, without liquid/cap mechanics.',
                               'Scale uncertainty is a prior, not a verified real-world error bound.'],
               'reviewed_geometry_report_sha256': sha256(root/'fit/geometry-validation.json')}
    write_json(root/'result.json', summary)
    report = f'''# 视频场景物理资产首版

整场景及三个物品独立审查均为 PASS。最终场景为 `simulation/scene.usda`，
桌面、瓶子、14 英寸 MacBook Pro、鼠标与独立支架；没有机器人或测试方块。
打开 USD 后播放物理。搬移时完整保留 `simulation/asset/` 和 `simulation/assets/`。

- 真实 USD 物理检查：{len(physics['checks'])} 项通过。
- 新进程重新加载检查：{len(reload['checks'])} 项通过；无需额外写入关节状态或姿态。
- 鼠标推动：{physics['mouse_push_displacement_m']*1000:.1f} mm；向上受力离桌约 {physics['mouse_lift_m']*100:.1f} cm。
- 瓶子可倾倒；笔记本被动铰链在重力下停留，外加力矩转动约 {abs(physics['hinge_torque_motion_deg']):.1f}°。
- 桌面约 {summary['table_size_m'][0]*100:.1f} × {summary['table_size_m'][1]*100:.1f} cm；高度 75 cm 为任务设计。
- 41 视角轮廓 IoU：瓶子 {geometry['assets']['asset-0001']['mean_iou']:.3f}、
  笔记本 {geometry['assets']['asset-0002']['mean_iou']:.3f}、鼠标 {geometry['assets']['asset-0003']['mean_iou']:.3f}。

交互过程：`simulation/interaction-preview.mp4`；初始场景：`simulation/frame-initial.png`。
三个候选的蓝图、`model.py`、独立 Review 选择的 QA 计划、`qa_tests.py` 与验收结果位于 `objects/`。
QA 依据冻结蓝图编译，不以常量 True 代替测试。整场景审查为 `scene-review.json`。

尺度来自用户确认的 14 寸 MBP 型号族、厂商尺寸先验及两视角角点拟合，仍非实测。
鼠标是最弱的几何项；质量、摩擦、铰链阻力和隐藏支架包含假设。
没有验证机器人抓取、液体、瓶盖拧动或鼠标按钮机制。
瓶身透明性与背面贴图仅作简化。像素轮廓指标不能证明真实接触尺寸精度。

本轮使用新的尺度与接触优先蓝图，来源为视频拟合和用户约束；早先设计、失败仿真及协议修复记录仍保留供追溯。
复现与参数说明见项目 `docs/VIDEO_ASSETS.md`。
'''
    (root/'report.md').write_text(report)
    approved = [root/'result.json', root/'report.md', root/'scene-review.json', root/'scene-build-spec.json',
                root/'scene-reload/validation.json']
    approved += [p for p in (root/'simulation').rglob('*') if p.is_file()]
    approved += [root/'fit'/n for n in ['annotations.json', 'scale-fit.json', 'geometry-validation.json', 'silhouette-comparison.jpg']]
    for asset in ['asset-0001', 'asset-0002', 'asset-0003']:
        work = root/'objects'/asset
        approved += [work/n for n in ['blueprint.json', 'build-spec.json', 'model.py', 'qa_tests.py', 'qa.json', 'review.json', 'build.json']]
        approved += [p for p in (work/'inputs').rglob('*') if p.is_file()]
    manifest = {str(p.relative_to(root)): {'sha256': sha256(p), 'bytes': p.stat().st_size} for p in approved}
    write_json(root/'artifact-manifest.json', {'format': 'reviewed-video-scene-v1', 'artifacts': manifest})
    archive = root/'scene-package.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for p in [*approved, root/'artifact-manifest.json']:
            z.write(p, p.relative_to(root))
    with zipfile.ZipFile(archive) as z:
        for ref, item in manifest.items():
            import hashlib
            if hashlib.sha256(z.read(ref)).hexdigest() != item['sha256']:
                raise ValueError('Package verification failed: '+ref)
    print(json.dumps({'package': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': sha256(archive), 'verified_files': len(manifest)}))


if __name__ == '__main__':
    main()
