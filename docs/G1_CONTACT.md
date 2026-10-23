# G1 接触演示

本轮通过运行：`outputs/user-g1-contact-20261023-v5/`。
视频为 `g1-interaction.mp4`，结果、接触力和物体位移见 `report.md`、`validation.json`、
`contact-diagnostics.png` 和 `trajectory.json`。中间失败运行保留用于追溯。

场景沿用已审查的三个物理资产。官方 G1 29 自由度 URDF 来自之前固定版本的
`outputs/user-video-isaac-20261023/references/`，包含 Unitree 许可及来源哈希。
原版橡胶手缺少碰撞，工作副本根据原视觉几何补齐碰撞，经 Isaac Lab URDF 转换器导入。

`construction/robot_kinematics.py` 读取实际 URDF 的父子变换、关节轴和行程计算 IK。
`construction/isaac_robot_worker.py` 使用关节 PD 与重力补偿执行路径。
物体不接收直接位姿、关节角或外力控制；运动由手部接触和摩擦产生。

路径包含桌外抬手、屏幕后方接触、推动屏幕、退到高位，再用另一只手按压并拖动鼠标。
右手在接近合盖时释放，原有低角度重力漂移继续完成闭合。接触传感器分别过滤
右手—屏幕、左手—鼠标，鼠标位移以拖动阶段计算，另检查全程仍在桌面。

```bash
.venv/bin/python scripts/run_table_sim.py --worker robot \
  --input outputs/user-scene-assets-20261023/simulation \
  --output outputs/user-g1-contact-replay

/home/felixz/.local/share/roboenv/isaac-env/bin/python \
  scripts/report_robot.py outputs/user-g1-contact-replay
```

这是固定骨盆、关闭机器人自碰撞的脚本演示，未接入站立平衡策略或自主任务规划。
橡胶手摩擦 1.2/1.0 为假设；机器人关节力矩限制沿用 URDF 等级，腕俯仰/偏航限制 5 Nm。
下一阶段应加入全身碰撞规划、力反馈闭环和更全面的成功判定，再考虑自由站立与学习策略。
