"""Recompile existing v9 geometry and compare every authored physics property.

Usage: python -m roboenv_perception.standard_interactions.migrate_v9
  --source .../delivery/triangular_box.usda --output outputs/new-candidate
"""

import argparse
import hashlib
import json
from pathlib import Path

from ..io import sha256, write_json
from .catalog import StandardComponentError, load_catalog
from .compiler import FASTENER_REF, PROTOCOL_REF, RUNTIME_REF, TOOL_REF, compile_connections
from .usd import author_connections


def physics_snapshot(stage):
    """Read USD independently of compiler fields, including geometry and all relationships."""
    result = {}
    for prim in stage.Traverse():
        attrs = {
            a.GetName(): str(a.Get())
            for a in prim.GetAuthoredAttributes()
            if a.GetName() != "envcreator:fastenerSpec"
        }
        result[str(prim.GetPath())] = {
            "type": prim.GetTypeName(),
            "schemas": list(prim.GetAppliedSchemas()),
            "attrs": attrs,
            "relationships": {
                r.GetName(): [str(p) for p in r.GetTargets()]
                for r in prim.GetAuthoredRelationships()
            },
        }
    return result


def migrate(source: Path, output: Path):
    from pxr import Usd

    if output.exists():
        raise ValueError("output already exists; preserve previous evidence")
    kit = load_catalog()
    source_record = next(
        s for s in kit["sources"] if s["path"].endswith("delivery/triangular_box.usda")
    )
    if sha256(source) != source_record["sha256"]:
        raise StandardComponentError("v9 source differs from the frozen tested asset")
    stage = Usd.Stage.Open(str(source.resolve()))
    if not stage:
        raise ValueError("cannot open source USD")
    bindings, originals = [], {}
    for i in range(3):
        path = f"/World/screw_{i}"
        old = json.loads(stage.GetPrimAtPath(path).GetAttribute("envcreator:fastenerSpec").Get())
        originals[str(i)] = old
        bindings.append(
            {
                "id": str(i),
                "standard_ref": FASTENER_REF,
                "tool_ref": TOOL_REF,
                "runtime_ref": RUNTIME_REF,
                "protocol_ref": PROTOCOL_REF,
                "screw": path,
                "support": old["moving_support"],
                "clamped_member": old["clamped_member"],
                "tool": old["tool"],
                "origin_m": old["origin_support_local_m"],
                "axis": old["axis_world"],
                "engagement_m": old["engagement_m"],
                "removal_distance_m": old["separation_m"],
                "evidence_refs": ["legacy:v9-delivery:" + source_record["sha256"]],
                "assumptions": ["Migrated existing exact bindings; no new image identification."],
            }
        )
    compiled = compile_connections(bindings)
    # Compare static physics parameters as well as the independently read USD properties.
    mismatches = []
    for row in compiled["connections"]:
        old = originals[row["id"]]
        for key, value in row["fastener_spec"].items():
            if key == "assumptions":
                continue
            if key not in old or old[key] != value:
                mismatches.append(f"{row['id']}.{key}")
    if mismatches:
        raise StandardComponentError(
            "frozen parameters differ from source: " + ", ".join(mismatches)
        )
    before = physics_snapshot(stage)
    output.mkdir(parents=True)
    target = output / "triangular_box.usda"
    stage.GetRootLayer().Export(str(target))
    rebuilt = Usd.Stage.Open(str(target.resolve()))
    author_connections(rebuilt, compiled)
    rebuilt.GetRootLayer().Save()
    rebuilt = Usd.Stage.Open(str(target.resolve()))
    after = physics_snapshot(rebuilt)
    changed = [
        path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)
    ]
    report = {
        "status": "equivalent" if not changed else "failed",
        "source": str(source.resolve()),
        "source_sha256": sha256(source),
        "asset_sha256": sha256(target),
        "compiled_sha256": compiled["compiled_sha256"],
        "changed_prims": changed,
        "parameter_comparison": "equal",
        "physics_status": "not_run",
        "scope": "all authored USD attributes, geometry, schemas and relationships equal; provenance metadata excluded",
        "historical_acceptance": "preserved; migration does not resolve existing failures",
    }
    write_json(output / "compiled_assembly.json", compiled)
    write_json(
        output / "test_plan.json",
        {
            "protocols": [row["components"]["protocol"] for row in compiled["connections"]],
            "status": "not_run",
        },
    )
    write_json(output / "migration.json", report)
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()
    }
    write_json(output / "manifest.json", {"files_sha256": files, "status": "candidate_unverified"})
    if changed:
        raise StandardComponentError("USD physics changed during migration; see migration.json")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
