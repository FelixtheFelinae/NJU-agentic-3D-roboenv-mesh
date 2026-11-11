"""Summarize a recorded G1 contact attempt without changing its physics result."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory
    report = json.loads((root/'validation.json').read_text())
    rows = json.loads((root/'trajectory.json').read_text())
    t = np.array([r['t'] for r in rows])
    q = np.array([r['hinge_deg'] for r in rows])
    mouse = np.array([r['mouse_position'] for r in rows])
    right = np.array([np.linalg.norm(r['filtered_hand_forces_N']['right'][0]) for r in rows])
    left = np.array([np.linalg.norm(r['filtered_hand_forces_N']['left'][1]) for r in rows])
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True, constrained_layout=True)
    axes[0].plot(t, q, color='#1864ab')
    axes[0].set_ylabel('Laptop opening (deg)')
    axes[1].plot(t, (mouse[:, 0]-mouse[0, 0])*1000, label='X displacement', color='#087f5b')
    axes[1].plot(t, (mouse[:, 1]-mouse[0, 1])*1000, label='Y displacement', color='#5c940d')
    axes[1].set_ylabel('Mouse movement (mm)')
    axes[1].legend()
    axes[2].plot(t, right, label='Right hand / display', color='#d9480f')
    axes[2].plot(t, left, label='Left hand / mouse', color='#6741d9')
    axes[2].set(xlabel='Simulation time (s)', ylabel='Filtered contact force (N)')
    axes[2].legend()
    for ax in axes:
        ax.grid(alpha=.2)
    fig.suptitle('G1 contact demo | fixed pelvis | IK + joint PD + gravity compensation')
    fig.savefig(root/'contact-diagnostics.png', dpi=160)
    plt.close(fig)
    text = f'''# G1 合盖与鼠标拖动演示

运行结果：**{report['status']}**，{sum(report['checks'].values())}/{len(report['checks'])} 项检查通过。

- 笔记本开角：{report['initial_lid_deg']:.1f}° → {report['final_lid_deg']:.1f}°。
- 鼠标有效拖动阶段：{report['mouse_drag_displacement_m']*100:.2f} cm；全程净位移 {report['mouse_displacement_m']*100:.2f} cm。
- 鼠标拖动阶段持续接触约 {report['mouse_drag_contact_duration_s']:.2f} 秒，全程留在桌面。
- 右手与屏幕峰值接触力 {report['right_hand_lid_peak_force_N']:.2f} N；左手与鼠标 {report['left_hand_mouse_peak_force_N']:.2f} N。

视频：`g1-interaction.mp4`；曲线：`contact-diagnostics.png`；原始数据：`trajectory.json`。

机器人来自已固定版本的官方 G1 29 自由度 URDF。橡胶手原文件只有视觉网格，派生副本补充了对应碰撞；
原场景物品参数保持原样。机器人通过 URDF IK、关节 PD 和重力补偿执行脚本路径，
物体运动来自手部碰撞与摩擦。右手推动屏幕接近合盖，释放后的重力运动继续完成闭合；
左手按压鼠标后横移并抬手。

本演示固定骨盆，机器人自碰撞关闭；未验证站立平衡、自主规划或真实硬件部署。
橡胶手静/动摩擦系数为假设的 1.2/1.0。笔记本原有近合盖漂移仍然保留。

复现（从项目根目录运行，输出目录必须不存在）：

```bash
.venv/bin/python scripts/run_table_sim.py --worker robot \\
  --input outputs/user-scene-assets-20261023/simulation \\
  --output outputs/user-g1-contact-replay
```

该命令需要本机已有的独立 Isaac Sim 运行环境及 `outputs/user-video-isaac-20261023/references`。
机器人资产在 `robot/g1.usd`，派生 URDF 和原始授权说明在 `robot-input/`。
'''
    (root/'report.md').write_text(text)
    files = ['validation.json', 'trajectory.json', 'ik-plan.json', 'robot-joint-names.json',
             'robot-body-names.json', 'g1-interaction.mp4', 'contact-diagnostics.png', 'report.md']
    manifest = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
    (root/'artifact-manifest.json').write_text(json.dumps({'format': 'g1-contact-demo-v1', 'sha256': manifest}, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'report': str(root/'report.md'), 'video': str(root/'g1-interaction.mp4')}))


if __name__ == '__main__':
    main()
