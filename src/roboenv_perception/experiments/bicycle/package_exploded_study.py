#!/usr/bin/env python3
"""Prepare labelled plates and a local review package from actual Blender renders."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GROUP_NAMES = {
    "frame": "焊接车架总成",
    "rear_wheel": "后轮 / 轴端",
    "front_wheel": "前轮 / 轴端",
    "fork": "伸缩前叉",
    "headset": "候选头碗组件",
    "cockpit": "车把 / 刹把 / 终端",
    "saddle": "鞍座 / 座管快拆",
    "drivetrain": "曲柄 / 中轴 / 链条 / 脚踏",
    "chainguard": "链罩与紧固件",
    "rear_fender": "后挡泥板及支撑",
    "front_fender": "前挡泥板及支撑",
    "basket": "车篮 / 杯架 / 安装座",
    "accessories": "轮锁外壳 / 侧脚撑",
}
DETAILS = {
    "hub-internals": (
        "轮毂与轴端：候选轴向堆叠",
        ["FW03", "FW04", "FW05L", "FW06L", "FW07L", "FW08"],
    ),
    "fork-headset": (
        "前叉与头碗：外部识别和内部候选",
        ["FK01", "FK02", "FK03L", "FK04L", "FK05L", "FK06L", "FK07L", "HS02"],
    ),
    "drivetrain": (
        "传动总成：中轴、曲柄和逐节链条",
        ["DT01", "DT02L", "DT04L", "DT05L", "DT06L", "DT07L", "DT10", "DT12"],
    ),
    "cockpit": (
        "车把总成：刹把枢轴与夹紧件",
        ["CK01", "CK02", "CK03L", "CK04L", "CK05L", "CK06L", "CK07L", "CK09"],
    ),
    "saddle": (
        "座管与鞍座：快拆、座弓和夹块",
        ["SD01", "SD02", "SD03", "SD04", "SD06-1", "SD07", "SD08", "SD09"],
    ),
}


def font(size):
    return ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", size)


def plate(model, name, title, labels, projection, output):
    canvas = Image.new("RGB", (2200, 1350), "#f5f7fa")
    rgb = Image.open(model / f"{name}.png").convert("RGB").resize((1600, 1120))
    canvas.paste(rgb, (0, 140))
    d = ImageDraw.Draw(canvas)
    d.text((45, 25), title, fill="#182b3e", font=font(38))
    d.text(
        (45, 85),
        "原视频重新识别 · 独立程序化建模 · 装配展示，未经动力学验证",
        fill="#506477",
        font=font(22),
    )
    for i, (key, label) in enumerate(labels, 1):
        if key in projection:
            x, y = projection[key]
            x = x * 1600
            y = y * 1120 + 140
            d.ellipse((x - 16, y - 16, x + 16, y + 16), fill="#213c55", outline="white", width=2)
            d.text((x, y - 1), str(i), font=font(17), fill="white", anchor="mm")
        y = 175 + (i - 1) * 72
        d.text((1640, y), f"{i:02d}  {label}", font=font(23), fill="#213c55")
        d.text((1685, y + 32), key, font=font(17), fill="#697c8f")
    d.rounded_rectangle((1625, 1145, 2165, 1305), radius=12, fill="#e4edf5")
    for i, line in enumerate(
        [
            "蓝色：不可见内部结构的候选件",
            "黄色 / 灰色：依照可见特征近似",
            "尺寸、重复件数量及配合尚未实测",
            "螺纹省略；不等于制造级 CAD",
        ]
    ):
        d.text((1645, 1160 + i * 33), line, font=font(22), fill="#214b70")
    canvas.save(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--model", default="model-v3")
    args = p.parse_args()
    root = args.root
    model = root / args.model
    delivery = root / "delivery"
    delivery.mkdir(exist_ok=True)
    rows = json.loads((model / "parts.json").read_text())
    by_id = {r["id"]: r for r in rows}
    plate(
        model,
        "exploded",
        "自行车分层爆炸图",
        list(GROUP_NAMES.items()),
        json.loads((model / "exploded-projection.json").read_text()),
        delivery / "整车爆炸图.png",
    )
    for name, (title, ids) in DETAILS.items():
        labels = [(i, by_id[i]["label_zh"]) for i in ids]
        plate(
            model,
            name,
            title,
            labels,
            json.loads((model / (name + "-parts-projection.json")).read_text()),
            delivery / (name + "-annotated.png"),
        )
    for name in [
        "assembly.blend",
        "assembled.glb",
        "exploded.glb",
        "assembled.png",
        "parts.csv",
        "parts.json",
        "validation.json",
        "assembly-explosion.mp4",
    ]:
        if (model / name).exists():
            shutil.copyfile(model / name, delivery / name)
    for name in ["observations.json", "evidence-sheet.jpg"]:
        shutil.copyfile(root / name, delivery / name)
    shutil.copytree(root / "evidence", delivery / "evidence", dirs_exist_ok=True)
    shutil.copyfile("docs/BICYCLE_EXPLODED_STUDY.md", delivery / "README.md")
    ledger = {
        "input": "Original RGB frames only; existing bicycle generators not imported",
        "steps": [
            {
                "step": "recognition",
                "result": "12 evidence regions with original image hashes and timestamps; hidden internals explicitly unobserved",
            },
            {
                "step": "construction",
                "result": "Independent procedural parts and reversible two-pose exploded layout; all dimensions approximate",
            },
            {
                "step": "v1 visual review",
                "finding": "Head tube and steerer center lines misaligned",
                "change": "Align new head tube and stem to fork datum; correct rear cable route approximation",
            },
            {
                "step": "v2 reload check",
                "finding": "Curve cap boundaries not welded; GLB exporter reset exploded objects to animation start",
                "evidence": "../model-v2/validation.json",
            },
            {
                "step": "v3 repair",
                "change": "Weld curve boundary vertices, use cyclic curves for closed loops; explicitly export current frame",
                "evidence": "validation.json",
            },
        ],
        "not_established": [
            "actual complete BOM",
            "precise dimensions and tolerances",
            "hidden bearing/brake/freewheel/lock/suspension implementation",
            "interference-free assembly",
            "collision-free disassembly trajectories",
            "dynamics or robot operation",
        ],
        "unresolved_parts": [
            "轮毂内部制动元件",
            "锁舌/执行器/锁芯",
            "终端电子元件",
            "前叉弹性与阻尼元件细分",
            "螺纹牙型与公差",
            "被遮挡的卡簧/止动件/垫圈",
            "轮胎内胎或实心结构",
            "鞍座材料分层",
            "完整走线和固定夹",
        ],
    }
    (delivery / "engineering-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
    )
    manifest = {
        str(f.relative_to(delivery)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(delivery.rglob("*"))
        if f.is_file() and f.name != "manifest.json"
    }
    (delivery / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    with zipfile.ZipFile(root / "自行车爆炸图探索包.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(delivery.rglob("*")):
            if f.is_file():
                z.write(f, str(f.relative_to(root)))
    print(f"Packaged {len(rows)} model objects; {len(manifest)} hashed files")


if __name__ == "__main__":
    main()
