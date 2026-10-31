"""Reuse existing Artisan compile/export and USD hierarchy normalization."""

import argparse
import json
from pathlib import Path

from roboenv_perception.construction.asset_agents import run_artisan

parser = argparse.ArgumentParser()
parser.add_argument("--spec", type=Path, required=True)
parser.add_argument("--geometry", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument(
    "--python",
    type=Path,
    default=Path("/home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python"),
)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
(args.output / "inputs").mkdir()
spec = json.loads(args.spec.read_text())
bodies = {"chassis": (0, 0, 0)}
for p in spec["parts"]:
    if p["body"] == p["id"]:
        bodies[p["body"]] = p["center"]
lines = [
    "from sdk import ArticulatedObject, mesh_from_input, Origin, MotionLimits, TestContext, TestReport",
    "def build_object_model():",
    "    m = ArticulatedObject(name='video_cart')",
]
for body in bodies:
    lines.append(f"    {body} = m.part({body!r}, est_mass_kg={2.0 if body == 'chassis' else 0.15})")
for p in spec["parts"]:
    name = p["id"]
    origin = bodies[p["body"]]
    offset = [p["center"][i] - origin[i] for i in range(3)]
    obj = []
    for row in (args.geometry / "meshes" / (name + ".obj")).read_text().splitlines():
        if row.startswith("v "):
            row = "v " + " ".join(str(float(x) + offset[i]) for i, x in enumerate(row.split()[1:]))
        obj.append(row)
    (args.output / "inputs" / (name + ".obj")).write_text("\n".join(obj) + "\n")
    lines.append(f"    {p['body']}.visual(mesh_from_input({name!r}), name={name!r})")
for body, origin in bodies.items():
    if body == "chassis":
        continue
    lines.append(
        f"    m.articulation({'spin_' + body!r}, 'continuous', parent='chassis', child={body!r}, origin=Origin(xyz={tuple(origin)!r}), axis=(1,0,0), motion_limits=MotionLimits(effort=1,velocity=30))"
    )
lines += [
    "    return m",
    "object_model = build_object_model()",
    "def run_tests() -> TestReport:",
    "    return TestContext(object_model).report()",
]
(args.output / "model.py").write_text("\n".join(lines) + "\n")
run_artisan(args.python, args.output)
print("Artisan exported", args.output)
