"""Build the Isaac asset from the same Blueprint and Blender recipe used by the Harness."""

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path

from ..construction.part_rules import joint_limits
from ..io import relative_file, sha256, write_json


def recipe_joints(blueprint, recipe):
    placements = {placement.joint: placement.origin_parent_m for placement in recipe.joint_placements}
    bounds = {name: 1000.0 for c in blueprint.couplings for name in (c.driver_joint, c.driven_joint)}
    for _ in blueprint.couplings:
        for c in blueprint.couplings:
            bounds[c.driver_joint] = min(bounds[c.driver_joint], bounds[c.driven_joint] / abs(c.ratio.value))
    rows = []
    for joint in blueprint.joints:
        limits = None
        if joint.name in bounds:
            limits = (-bounds[joint.name], bounds[joint.name])
        elif joint.type in {"revolute", "prismatic"}:
            limits = joint_limits(joint.travel.value, joint.rest)
        rows.append({"name": joint.name, "type": joint.type, "parent": joint.parent,
                     "child": joint.child, "axis": None if joint.type == "fixed" else joint.axis_child,
                     "origin_parent_m": placements[joint.name], "limits": limits})
    return rows


def author_usd(output, usd_python, recipe, built, positions, *, joints=(), couplings=(),
               threads=(), ground=False, contact_offset_m=None, timeout_seconds=600):
    """Bind geometry and numeric rules into a USD-only subprocess request."""
    masses = recipe.part_masses_kg
    if set(positions) != set(masses) or any(
        not math.isfinite(mass) or mass <= 0 for mass in masses.values()
    ) or any(not math.isfinite(value) for position in positions.values() for value in position):
        raise ValueError("every part needs one finite reference position and positive mass")
    solids = {s.name: s for s in recipe.solids}
    collision_sources = {m["collision_for"] for m in built["meshes"] if "collision_for" in m}
    rows = []
    for mesh in built["meshes"]:
        proxy = mesh.get("collision_for")
        solid = solids[proxy or mesh["name"]]
        if mesh["part"] != solid.part:
            raise ValueError("Blender mesh has incorrect part identity")
        path = relative_file(output / "visual", mesh["file"])
        if sha256(path) != mesh["sha256"]:
            raise ValueError("Blender mesh changed before USD writing")
        if proxy:
            approximation = "convexHull"
        elif solid.name in collision_sources:
            approximation = None
        elif solid.shape == "annulus":
            raise ValueError("annulus collider sectors missing; a single convex hull fills the bore")
        elif solid.shape in {"hollow_box", "hollow_cylinder", "hollow_prism", "torus", "lathe", "frustum"}:
            approximation = "convexDecomposition"
        else:
            approximation = "convexHull"
        rows.append({"name": mesh["name"], "part": solid.part, "path": str(path.resolve()),
                     "rgba": mesh["rgba"], "collision_only": proxy is not None,
                     "approximation": approximation})
    if {m["name"] for m in rows if not m["collision_only"]} != set(solids):
        raise ValueError("Blender output differs from the declared recipe")
    request = output / "usd_request.json"
    write_json(request, {
        "parts": [{"id": name, "position_m": position, "mass_kg": masses[name]}
                  for name, position in positions.items()],
        "meshes": rows, "joints": list(joints), "couplings": list(couplings),
        "threads": list(threads), "ground": ground, "contact_offset_m": contact_offset_m,
    })
    usd = output / "asset.usda"
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LD_LIBRARY_PATH")
           if key in os.environ}
    with (output / "usd.log").open("w") as log:
        try:
            subprocess.run([str(usd_python), str(Path(__file__).with_name("usd_writer.py")),
                            str(request), str(usd)], stdout=log, stderr=subprocess.STDOUT,
                           env=env, timeout=timeout_seconds, check=True)
        except subprocess.SubprocessError as error:
            raise RuntimeError(f"OpenUSD writing failed; see {output / 'usd.log'}") from error
    if not usd.is_file():
        raise FileNotFoundError(usd)
    previews = [str((output / "visual" / name).resolve()) for name in built["previews"]]
    identity = {"model": sha256(request), "recipe": sha256(output / "recipe.json"),
                "usd": sha256(usd), "previews": {Path(p).name: sha256(Path(p)) for p in previews}}
    result = {
        "status": "compiled", "backend": "blender-openusd", "physics_status": "not_run",
        "usd": str(usd.resolve()), "usd_sha256": identity["usd"],
        "model_ref": str(request.resolve()), "model_sha256": identity["model"],
        "recipe_sha256": identity["recipe"], "visual_previews": previews,
        "candidate_sha256": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        "physics_notes": ["Declared mass; centre of mass and inertia inferred from colliders by PhysX.",
                          "Direct USD asset has not been physically validated."],
    }
    write_json(output / "candidate.json", result)
    return result
