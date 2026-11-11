"""Versioned material and solver-thread candidate. Run with USD Python bindings."""

import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Gf, Usd, UsdPhysics, UsdShade


def mesh_properties(path, density):
    vertices = []
    faces = []
    for line in path.read_text().splitlines():
        a = line.split()
        if a and a[0] == "v":
            vertices.append(list(map(float, a[1:4])))
        if a and a[0] == "f":
            faces.append([int(i.split("/")[0]) - 1 for i in a[1:]])
    volume = 0.0
    first = np.zeros(3)
    second = np.zeros((3, 3))
    v = np.array(vertices)
    for face in faces:
        for i in range(1, len(face) - 1):
            a, b, c = v[[face[0], face[i], face[i + 1]]]
            dv = np.dot(a, np.cross(b, c)) / 6
            sm = a + b + c
            volume += dv
            first += dv * sm / 4
            second += (
                dv * (np.outer(sm, sm) + np.outer(a, a) + np.outer(b, b) + np.outer(c, c)) / 20
            )
    if volume < 0:
        volume = -volume
        first = -first
        second = -second
    center = first / volume
    mass = volume * density
    inertia = density * (np.eye(3) * np.trace(second) - second) - mass * (
        np.eye(3) * np.dot(center, center) - np.outer(center, center)
    )
    eig, axes = np.linalg.eigh(inertia)
    if np.linalg.det(axes) < 0:
        axes[:, 0] *= -1
    rotation = Gf.Matrix3d(*axes.T.flatten().tolist()).ExtractRotation().GetQuat()
    return mass, center, eig, rotation, volume


def build(source, output, pretension=2500.0):
    from roboenv_perception.standard_interactions.catalog import StandardComponentError, resolve
    from roboenv_perception.standard_interactions.compiler import (
        FASTENER_REF,
        PROTOCOL_REF,
        RUNTIME_REF,
        TOOL_REF,
        compile_connections,
    )
    from roboenv_perception.standard_interactions.usd import author_connections

    runtime = resolve("runtimes", RUNTIME_REF)
    if pretension != runtime["solver_thread"]["pretension_N"]:
        raise StandardComponentError(
            "pretension is locked; register a new runtime instead of per-asset tuning"
        )
    output.mkdir(parents=True, exist_ok=False)
    stage = Usd.Stage.Open(str(source / "triangular_box.usda"))
    stage.GetRootLayer().Export(str(output / "triangular_box.usda"))
    stage = Usd.Stage.Open(str(output / "triangular_box.usda"))
    properties = {}
    result = json.loads((source / "geometry/result.json").read_text())
    steel = resolve("fasteners", FASTENER_REF)["density_kg_m3"]
    for row in result["meshes"]:
        name = row["part"]
        density = steel if name.startswith("screw_") else 2700.0
        mass, com, inertia, axes, volume = mesh_properties(
            source / "geometry" / row["file"], density
        )
        prim = stage.GetPrimAtPath("/World/" + name)
        if name.startswith("screw_"):
            com -= json.loads(prim.GetAttribute("envcreator:fastenerSpec").Get())["origin_world_m"]
        api = UsdPhysics.MassAPI.Apply(prim)
        api.CreateMassAttr(mass)
        api.CreateCenterOfMassAttr(Gf.Vec3f(*com))
        api.CreateDiagonalInertiaAttr(Gf.Vec3f(*inertia))
        api.CreatePrincipalAxesAttr(Gf.Quatf(axes))
        properties[name] = {
            "mass_kg": mass,
            "center_of_mass_local_m": com.tolist(),
            "principal_inertia_kg_m2": inertia.tolist(),
            "density_kg_m3": density,
            "volume_m3": volume,
        }
    # Object material is a separate unmeasured assumption. Thread mechanics are locked.
    for name, mu in [("Material", 0.45), ("SteelContact", runtime["solver_thread"]["thread_mu"])]:
        mat = UsdShade.Material.Define(stage, "/World/" + name)
        api = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        api.CreateStaticFrictionAttr(mu)
        api.CreateDynamicFrictionAttr(mu * 0.85)
        api.CreateRestitutionAttr(0.0)
    bindings = []
    for i in range(3):
        prim = stage.GetPrimAtPath(f"/World/screw_{i}")
        old = json.loads(prim.GetAttribute("envcreator:fastenerSpec").Get())
        bindings.append(
            {
                "id": str(i),
                "standard_ref": FASTENER_REF,
                "tool_ref": TOOL_REF,
                "runtime_ref": RUNTIME_REF,
                "protocol_ref": PROTOCOL_REF,
                "screw": str(prim.GetPath()),
                "support": "/World/body",
                "clamped_member": "/World/lid",
                "tool": old["tool"],
                "origin_m": old["origin_world_m"],
                "engagement_m": old["engagement_m"],
                "removal_distance_m": old["separation_m"],
                "evidence_refs": ["legacy:triangular-box-instance-layout"],
                "assumptions": [
                    "Existing user-dimensioned three-hole layout; no new visual identification."
                ],
            }
        )
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(
            UsdShade.Material.Get(stage, "/World/SteelContact"), materialPurpose="physics"
        )
    compiled = compile_connections(bindings)
    author_connections(stage, compiled)
    for i in range(3):
        for name, reason in [
            (f"lead_{i}", "numerical lead-coordinate carrier"),
            (f"thread_rotor_{i}", "numerical angular-coordinate carrier"),
        ]:
            properties[name] = {
                "mass_kg": runtime["numerical_carrier"]["mass_kg"],
                "reason": reason + "; no collision geometry",
            }
    stage.GetRootLayer().Save()
    (output / "compiled_assembly.json").write_text(json.dumps(compiled, indent=2))
    (output / "mass-properties.json").write_text(json.dumps(properties, indent=2))
    (output / "design.json").write_text(
        json.dumps(
            {
                "status": "candidate-unverified",
                "source": str(source),
                "materials": "aluminium enclosure 2700kg/m3; registered steel screw density",
                "standard_runtime_ref": RUNTIME_REF,
                "compiled_sha256": compiled["compiled_sha256"],
                "physics_status": "not_run",
                "mass_properties": properties,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--pretension", type=float, default=2500.0)
    a = p.parse_args()
    build(a.source, a.output, a.pretension)
