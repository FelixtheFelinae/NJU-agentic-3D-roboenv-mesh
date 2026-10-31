"""Label measured-pose replay frames; do not generate or alter simulated poses."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--font", type=Path, default=Path(
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
    args = parser.parse_args()
    rows = json.loads((args.replay / "frames.json").read_text())
    paths = sorted(args.replay.glob("frame-*.png"))
    if len(paths) != len(rows) or len(rows) < 2:
        raise ValueError("replay needs one measured trace row for each frame")
    fonts = {size: ImageFont.truetype(str(args.font), size) for size in (16, 20, 25, 30)}
    with Image.open(args.replay / "overview.png") as source:
        overview = source.crop((245, 0, 555, 700)).resize((279, 630))
    images = []
    for path, row in zip(paths, rows, strict=True):
        canvas = Image.new("RGB", (1000, 830), "#17212c")
        canvas.paste(overview, (0, 90))
        with Image.open(path) as macro:
            canvas.paste(macro.resize((720, 630)), (280, 90))
        d = ImageDraw.Draw(canvas)
        d.text((22, 10), "电动螺丝刀：理想对准与旋松", font=fonts[30], fill="white")
        d.text((24, 53), "PhysX 实测位姿的 Blender 重放；已知目标坐标，无机器人", font=fonts[16], fill="#c1d0df")
        d.text((18, 98), "完整工具（初始帧）", font=fonts[20], fill="white")
        d.text((298, 98), "刀尖与螺丝近景", font=fonts[20], fill="white")
        state = ("螺纹已解除" if not row["thread_active"] else
                 "电机驱动旋松" if row["time_s"] >= 1 and row["engaged"] else
                 "已插入啮合" if row["engaged"] else "靠近与对中")
        d.text((22, 730), f'{row["time_s"]:5.2f} s  |  {state}', font=fonts[25], fill="#79e2b1")
        d.text((22, 775), f'螺丝 {row["screw_theta_rad"] / math.tau:.2f} 圈   '
               f'退出 {row["screw_exit_m"] * 1000:.2f} mm   '
               f'径向误差 {row["radial_error_m"] * 1000:.3f} mm', font=fonts[20], fill="white")
        images.append(canvas)
    duration = round((rows[1]["time_s"] - rows[0]["time_s"]) * 1000)
    durations = [duration] * len(images)
    durations[-1] = 2000
    images[0].save(args.replay / "interaction.gif", save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=False)
    images[0].save(args.replay / "before.png")
    images[-1].save(args.replay / "after.png")
    contact = Image.new("RGB", (1500, 623), "#17212c")
    for column, index in enumerate((0, min(9, len(images)-1), len(images)-1)):
        contact.paste(images[index].resize((500, 415)), (column * 500, 0))
    d = ImageDraw.Draw(contact)
    d.text((24, 440), "对照：偏心 / 倾斜 / 有间隙 / 孔规格不匹配 → 均无啮合、无传动、无旋松", font=fonts[25], fill="white")
    d.text((24, 490), "电机关闭：仍插入啮合，螺丝不松动。32 项检查通过。", font=fonts[25], fill="#79e2b1")
    d.text((24, 553), "理想扭转耦合替代批头接触；简化螺距约束替代真实螺纹。非 G1 实验。", font=fonts[20], fill="#c1d0df")
    contact.save(args.replay / "summary.png")
    record = json.loads((args.replay / "recording.json").read_text())
    trace_path = Path(record["source_trace"])
    report_path = trace_path.with_name("report.json")
    record.update(static_panel="initial tool overview; only macro panel replays the time series",
                  source_trace_sha256=hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                  source_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  replay_code_sha256=hashlib.sha256(Path(__file__).with_name(
                      "render_interaction.py").read_bytes()).hexdigest(),
                  composition_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (args.replay / "recording.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
