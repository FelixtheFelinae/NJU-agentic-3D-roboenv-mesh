#!/usr/bin/env python3
"""Copy original RGB evidence and record image-space observations, not old geometry."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OBSERVATIONS = [
    (
        "E01",
        15,
        [420, 750, 1110, 1000],
        "传动外侧",
        "可见链罩、曲柄端紧固件、罩体螺钉及下侧链段。链轮齿数、中轴结构不可见。",
    ),
    (
        "E02",
        0,
        [440, 525, 685, 735],
        "前轮轴端",
        "可见轴端、紧固外形和黑色轮毂侧盖；内部制动机构及轴承类型未确认。",
    ),
    ("E03", 15, [705, 490, 895, 645], "座管夹", "可见夹环、快拆手柄及枢轴端面；夹紧螺杆尺寸未知。"),
    (
        "E04",
        15,
        [940, 0, 1640, 245],
        "车把控制件",
        "可见两刹把、夹座、转轴端面、握把和中央外壳；壳内电路未知。",
    ),
    (
        "E05",
        0,
        [1160, 655, 1550, 950],
        "后轮连接件",
        "可见挡泥板支撑固定点、脚撑座螺钉、后轴附近外壳。隐藏连接件数量未知。",
    ),
    (
        "E06",
        15,
        [1270, 425, 1560, 975],
        "伸缩前叉",
        "可见叉冠、左右内管、外管、管口密封边界及轮轴端；弹性元件种类不可见。",
    ),
    (
        "E07",
        24,
        [720, 225, 1380, 790],
        "车篮与前附件",
        "可见整体网格篮、后壁外壳、前反光件和底部网片；不能把每根网格算成可拆件。",
    ),
    (
        "E08",
        15,
        [1250, 305, 1500, 535],
        "杯架与走线",
        "可见开槽杯架和线管；隐藏螺母与穿线内部未知。",
    ),
    (
        "E09",
        0,
        [1070, 250, 1430, 430],
        "鞍座",
        "可见座面和黄色底缘；填充材料、底壳及轨夹内部未被完整拍摄。",
    ),
    (
        "E10",
        0,
        [990, 615, 1675, 910],
        "后挡泥板与轮锁区域",
        "可见后挡泥板、后反光件、支架及后轮上方壳体；锁芯及执行机构未知。",
    ),
    ("E11", 15, [1005, 750, 1150, 865], "脚踏", "可见框式踏板和反光片；内部轴套或轴承配置不可见。"),
    (
        "E12",
        18,
        [0, 0, 1920, 1080],
        "整车比例",
        "用于复核车架轮廓、车轮和附件位置。无标尺；尺寸均为建模近似，不是测量值。",
    ),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Original inputs directory with frames and video.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 19)
    records = json.loads((args.source / "video.json").read_text())["frames"]
    annotations = []
    sheet = Image.new("RGB", (1600, 1320), "#f5f6f8")
    draw = ImageDraw.Draw(sheet)
    for i, (key, index, box, title, observation) in enumerate(OBSERVATIONS):
        source = args.source / "frames" / f"{index:06d}.png"
        dest = out / source.name
        if not dest.exists():
            shutil.copyfile(source, dest)
        rgb = Image.open(source).convert("RGB")
        crop = rgb.crop(box)
        crop.save(out / f"{key}.png")
        crop.thumbnail((510, 272))
        x, y = (i % 3) * 533, (i // 3) * 330
        sheet.paste(crop, (x + 10, y + 10))
        draw.text(
            (x + 10, y + 290),
            f"{key} | frame {index:02d} | {records[index]['source_timestamp_seconds']:.3f}s",
            font=font,
            fill="#162938",
        )
        annotations.append(
            {
                "id": key,
                "title": title,
                "observation": observation,
                "source_image": f"evidence/{dest.name}",
                "sha256": sha(dest),
                "crop_xyxy": box,
                "crop": f"evidence/{key}.png",
                "source_timestamp_seconds": records[index]["source_timestamp_seconds"],
            }
        )
    sheet.save(args.output / "evidence-sheet.jpg", quality=94)
    manifest = {
        "method": "Fresh direct multiview RGB inspection; no prior asset geometry, segmentation labels or dimensions used",
        "video_sha256": sha(args.source / "source.mp4"),
        "observations": annotations,
        "scale": "unmeasured; nominal wheel outer diameter 0.61 m is a new modeling prior",
        "hidden_internals": "candidate topology only, not visually identified",
        "not_used": [
            "previous bicycle mesh",
            "previous part counts",
            "previous drivetrain ratio",
            "SAM to infer hidden parts",
            "generated detail enhancement",
        ],
    }
    (args.output / "observations.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
