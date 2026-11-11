"""Create inspectable hinge angle/torque plots and a concise experiment report."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    from roboenv_perception.construction import hinge

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory
    report = json.loads((root/'validation.json').read_text())
    traces = json.loads((root/'trajectory.json').read_text())
    report['cases'] = [hinge.evaluate_hinge_case(t['protocol'], t['samples'],
                        *np.radians(report['limits_deg'])) for t in traces]
    report['checks'] = {r['name']: r['status'] == 'passed' for r in report['cases']} | {
        'reviewed_scene_and_assets_unchanged': all(
            hashlib.sha256(Path(p).read_bytes()).hexdigest() == expected for p, expected in report['source_hashes'].items())}
    report['status'] = 'passed' if all(report['checks'].values()) else 'failed'
    report['analysis_method'] = 'Release speed is the maximum of DOF velocity and finite differences of joint angles.'
    report['raw_report_sha256'] = hashlib.sha256((root/'validation.json').read_bytes()).hexdigest()
    report['analysis_source_sha256'] = hashlib.sha256(Path(hinge.__file__).read_bytes()).hexdigest()
    (root/'audited-validation.json').write_text(json.dumps(report, indent=2)+'\n')
    count = len(traces)
    nrows = (count+1)//2
    fig, axes = plt.subplots(nrows, 2, figsize=(14, 3*nrows), constrained_layout=True)
    for ax, trial, result in zip(axes.flat, traces, report['cases']):
        samples = trial['samples']
        t = [r['t'] for r in samples]
        q = np.degrees([r['q_rad'] for r in samples])
        torque = [r['torque_Nm'] for r in samples]
        ax.plot(t, q, color='#1864ab', linewidth=2, label='angle')
        twin = ax.twinx()
        twin.plot(t, torque, color='#d9480f', alpha=.65, linewidth=1.5, label='torque')
        twin.set_ylim(-1.5, 1.5)
        twin.set_ylabel('Torque (Nm)', color='#d9480f')
        ax.set(xlabel='Trial time (s)', ylabel='Opening angle (deg)')
        ax.set_title(result['name']+' | '+result['status'].upper(),
                     color='#2b8a3e' if result['status'] == 'passed' else '#c92a2a')
        ax.grid(alpha=.2)
    for ax in axes.flat[count:]:
        ax.set_visible(False)
    fig.suptitle('Reviewed laptop USD | free base on stand | passive hinge | unchanged parameters', fontsize=14)
    fig.savefig(root/'hinge-diagnostics.png', dpi=160)
    plt.close(fig)
    rows = []
    for result in report['cases']:
        rows.append(f'| {result["name"]} | {result["applied_torque_Nm"]:+.2f} | '
                    f'{result["before_force_deg"]:.2f} → {result["after_force_deg"]:.2f} → {result["final_deg"]:.2f} | '
                    f'{result["release_max_speed_deg_s"]:.3f} | {result["base_horizontal_drift_mm"]:.2f} | {result["status"]} |')
    failures = [r['name']+': '+', '.join(k for k, v in r['checks'].items() if not v)
                for r in report['cases'] if r['status'] != 'passed']
    text = '# 笔记本转轴交互专项测试\n\n'
    text += f'结果：{report["status"]}。沿用已审查 USD、原始摩擦和限位参数；底座未固定，无位置伺服。\n\n'
    text += '每组先初始化角度与底座状态，然后由等大反向的两片刚体力矩驱动。释放阶段外加力矩为零。\n\n'
    text += '| 测试 | 外加力矩 Nm | 受力前 → 受力后 → 最终开角 ° | 释放后最大角速度 °/s | 底座平移 mm | 结果 |\n'
    text += '| --- | ---: | --- | ---: | ---: | --- |\n'+'\n'.join(rows)+'\n\n'
    text += '判据：停留/小力矩漂移 <2°；主动外力矩产生正确方向 >10° 转动；释放 0.3 秒后速度 <1°/s；限位越界 <2°。\n'
    text += '端点测试还要求实际到达目标限位；底座高度不得低于 0.80 m，水平漂移 <20 mm。\n\n'
    if failures:
        text += '未通过项：\n\n'+'\n'.join('- '+f for f in failures)+'\n\n'
    text += '当前测试确认的是假设参数下的仿真交互行为，未标定真实 MacBook 的铰链阻力或极限角。\n'
    text += '速度同时使用仿真 DOF 读数与角度差分，取较大值；近合盖时两者存在明显差异。\n'
    if any(c['name'] == 'close_release' and c['final_at_hard_stop'] for c in report['cases']):
        text += '原 close_release 长脉冲触及了闭合限位，单独不能证明中间角度松手可停；另用 followup 的 close_short_release 验证。\n'
    text += f'原场景和资产文件未修改：{report["checks"]["reviewed_scene_and_assets_unchanged"]}。完整来源哈希与逐步数据见 validation.json、trajectory.json。\n'
    (root/'report.md').write_text(text)
    print(json.dumps({'status': report['status'], 'failures': failures, 'report': str(root/'report.md')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
