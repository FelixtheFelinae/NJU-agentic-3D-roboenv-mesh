"""Build the video-conditioned Blender bicycle as an Artisan articulated asset."""

import argparse
import json
import shutil
from pathlib import Path

from build_ipad_audio_assets import build

from roboenv_perception.construction.blender_backend import file_hash, validate_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meshes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--artisan-python",
        type=Path,
        default=Path("/home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python"),
    )
    args = parser.parse_args()
    result = validate_result(args.meshes)
    drivetrain = any(m["name"] == "drive_chain_envelope" for m in result["meshes"])
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "inputs").mkdir()
    parts = {"frame": 14.0, "steering": 3.5, "rear_wheel": 2.0, "front_wheel": 2.0, "crank": 0.8}
    lines = [
        "from sdk import ArticulatedObject, mesh_from_input, Origin, MotionLimits, MotionProperties, TestContext, TestReport",
        "",
        "def build_object_model():",
        "    m = ArticulatedObject(name='yellow_bicycle')",
    ]
    for part, mass in parts.items():
        lines.append(f"    {part} = m.part({part!r}, est_mass_kg={mass!r})")
    for mesh in result["meshes"]:
        name = mesh["name"]
        if name.startswith("drive_"):
            # These closed solids depict an ideal continuous chain mechanism.
            # They are attached after SDK export, explicitly visual-only; their
            # physics is the native coupling below, not contact between solids.
            continue
        # The SDK's OBJ reader splits vertices on per-corner normal seams.
        # Preserve topology for physics by retaining only position indices;
        # original Blender meshes and their smooth normals remain unchanged.
        obj_lines = []
        for line in (args.meshes / mesh["file"]).read_text().splitlines():
            if line.startswith("v "):
                obj_lines.append(line)
            elif line.startswith("f "):
                obj_lines.append("f " + " ".join(x.split("/")[0] for x in line.split()[1:]))
        (output / "inputs" / mesh["file"]).write_text("\n".join(obj_lines) + "\n")
        lines.extend(
            [
                f"    finish = m.material({name!r}, rgba={tuple(mesh['rgba'])!r}, material_class={mesh['material_class']!r})",
                f"    {mesh['part']}.visual(mesh_from_input({name!r}), material=finish, name={name!r})",
            ]
        )
    # Child local meshes have already been shifted by their assembled origins.
    # The steering axis follows the inclined head tube; front axle is relative
    # to steering, ensuring the wheel travels with the fork when steered.
    joints = [
        dict(
            name="steer",
            parent="frame",
            child="steering",
            origin=[0.35, 0, 0.82],
            axis=[-0.318, 0, 0.948],
            limits=[-0.65, 0.65],
        ),
        dict(
            name="rear_spin",
            parent="frame",
            child="rear_wheel",
            origin=[-0.54, 0, 0.30],
            axis=[0, 1, 0],
            limits=None,
        ),
        dict(
            name="front_spin",
            parent="steering",
            child="front_wheel",
            origin=[0.19, 0, -0.52],
            axis=[0, 1, 0],
            limits=None,
        ),
        dict(
            name="pedal_spin",
            parent="frame",
            child="crank",
            origin=[-0.015, 0, 0.285],
            axis=[0, 1, 0],
            limits=None,
        ),
    ]
    for j in joints:
        limits = (
            f"MotionLimits(lower={j['limits'][0]},upper={j['limits'][1]},effort=10,velocity=20)"
            if j["limits"]
            else "MotionLimits(effort=10,velocity=20)"
        )
        lines.append(
            f"    m.articulation({j['name']!r}, {'revolute' if j['limits'] else 'continuous'!r}, parent={j['parent']!r}, child={j['child']!r}, origin=Origin(xyz={tuple(j['origin'])!r}), axis={tuple(j['axis'])!r}, motion_limits={limits}, motion_properties=MotionProperties(damping=0.03,friction=0.01))"
        )
    if drivetrain:
        lines.append("    m.couple('pedal_spin', 'rear_spin', mapping=2.5, sense='same')")
    lines.extend(
        [
            "    return m",
            "",
            "object_model = build_object_model()",
            "",
            "def run_tests() -> TestReport:",
            "    ctx = TestContext(object_model)",
        ]
    )
    lines.extend(["    return ctx.report()", ""])
    (output / "model.py").write_text("\n".join(lines))
    spec = {
        "asset_id": "yellow_bicycle",
        "category": "bicycle",
        "kind": "articulated",
        "parts": [{"name": p, "est_mass_kg": m} for p, m in parts.items()],
        "joints": joints,
        "dimensions_m": [result["bounds_m"][1][i] - result["bounds_m"][0][i] for i in range(3)],
        "blender_result": str((args.meshes / "result.json").resolve()),
        "blender_result_sha256": file_hash(args.meshes / "result.json"),
        "scale_source": "estimated from video proportions with assumed 0.30 m wheel radius",
        "scale_relative_uncertainty": 0.15,
        "assumptions": [
            "No measured dimensions or mass. Total 22.3 kg is a modeling prior.",
            "Independent wheel, steering and crank hinges; no chain transmission, brake or pedal bearing model.",
            "Parking stand is fixed deployed; hidden details simplified.",
            "Surface colors approximated; logos and text are not reconstructed.",
        ],
    }
    if drivetrain:
        spec["drivetrain"] = {
            "type": "ideal_fixed_gear",
            "ratio": 2.5,
            "source": "assumed, not measured",
            "freewheel": False,
            "visual_only_meshes": [m for m in result["meshes"] if m["name"].startswith("drive_")],
        }
        spec["assumptions"][1] = (
            "Ideal fixed-gear chain coupling; no freewheel, slack, per-link collision or brakes."
        )
    (output / "build-spec.json").write_text(json.dumps(spec, indent=2) + "\n")
    report = build(output, args.artisan_python)
    if drivetrain:
        folder = output / "export/drivetrain"
        folder.mkdir()
        for mesh in spec["drivetrain"]["visual_only_meshes"]:
            shutil.copyfile(args.meshes / mesh["file"], folder / mesh["file"])
        (folder / "manifest.json").write_text(json.dumps(spec["drivetrain"], indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
