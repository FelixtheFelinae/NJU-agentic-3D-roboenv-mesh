"""Compose exported Artisan USD assets into a provisional physical scene.

The input image supplies ordering and approximate support-relative positions only.
There is no camera calibration, recovered depth, or validated contact in this step.
Assets are referenced without scale changes so authored inertia and joints remain
in their original metre units. Isaac must reload and test the resulting scene.
"""

import math
import re
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..construction.common import new_run
from ..contracts import Contract
from ..io import relative_file, sha256, write_json

_ID = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
_SOURCE = Literal["measured", "estimated", "assumed"]


def _valid_box(box: tuple[int, int, int, int], size: tuple[int, int]) -> bool:
    x0, y0, x1, y1 = box
    width, height = size
    return 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height


class SceneAsset(Contract):
    asset_id: str = Field(pattern=_ID)
    kind: Literal["rigid", "articulated"]
    usd_path: Path
    frame_id: str = Field(min_length=1)
    bbox_xyxy: tuple[int, int, int, int]
    image_size_xy: tuple[int, int]
    local_bounds_min_m: tuple[float, float, float]
    local_bounds_max_m: tuple[float, float, float]
    bounds_source: _SOURCE = "estimated"
    evidence_refs: list[str] = Field(min_length=1)
    bounds_note: str = "Geometry bounds supplied by the model; physical contact not verified."
    metres_per_unit: Literal[1.0] = 1.0

    @model_validator(mode="after")
    def valid_geometry(self):
        if not _valid_box(self.bbox_xyxy, self.image_size_xy):
            raise ValueError("asset bbox must fit its source image")
        if not all(
            hi > lo for lo, hi in zip(self.local_bounds_min_m, self.local_bounds_max_m, strict=True)
        ):
            raise ValueError("asset local bounds must have positive XYZ extent")
        if not self.bounds_note.strip():
            raise ValueError("asset bounds need a provenance note")
        return self


class SupportSpec(Contract):
    frame_id: str = Field(min_length=1)
    image_size_xy: tuple[int, int]
    image_bbox_xyxy: tuple[int, int, int, int]
    size_x_m: float = Field(gt=0, le=100)
    size_y_m: float = Field(gt=0, le=100)
    top_z_m: float = Field(ge=-10, le=100)
    thickness_m: float = Field(default=0.04, gt=0, le=2)
    dimension_source: _SOURCE = "assumed"
    evidence_refs: list[str] = Field(min_length=1)
    dimension_note: str = "Task-scale support surface; image-to-world mapping is provisional."
    static_friction: float = Field(default=0.6, ge=0, le=2)
    dynamic_friction: float = Field(default=0.5, ge=0, le=2)
    restitution: float = Field(default=0, ge=0, le=1)
    contact_parameter_source: _SOURCE = "assumed"
    contact_note: str = "Provisional contact parameters; no friction or restitution measurement."
    clearance_m: float = Field(default=0.005, ge=0, le=0.1)

    @model_validator(mode="after")
    def coherent(self):
        if not _valid_box(self.image_bbox_xyxy, self.image_size_xy):
            raise ValueError("support bbox must fit the source image")
        if self.dynamic_friction > self.static_friction:
            raise ValueError("dynamic friction cannot exceed static friction")
        if not self.dimension_note.strip():
            raise ValueError("support dimensions need a provenance note")
        if not self.contact_note.strip():
            raise ValueError("support contact parameters need a provenance note")
        return self


def scene_asset_from_evidence(
    asset_id: str,
    usd_path: Path,
    evidence: dict,
    blender_result: dict,
    frame_id: str,
    image_size_xy: tuple[int, int],
    *,
    kind: Literal["rigid", "articulated"],
    bounds_source: _SOURCE = "estimated",
) -> SceneAsset:
    """Bind one selected evidence view and Blender's preview bounds to a USD.

    The Blender bound may omit joint motion and is a placement aid, not a USD
    collision or articulation bound. The caller chooses a common source frame.
    """
    views = [view for view in evidence.get("views", []) if view.get("frame_id") == frame_id]
    if len(views) != 1:
        raise ValueError(f"expected exactly one evidence view for {asset_id} in {frame_id}")
    bounds = blender_result.get("bounds_m")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("Blender result lacks bounds_m")
    if evidence.get("asset_id") != asset_id:
        raise ValueError("evidence asset_id differs from requested asset")
    return SceneAsset(
        asset_id=asset_id,
        kind=kind,
        usd_path=usd_path,
        frame_id=frame_id,
        bbox_xyxy=tuple(views[0]["bbox_xyxy"]),
        image_size_xy=image_size_xy,
        local_bounds_min_m=tuple(bounds[0]),
        local_bounds_max_m=tuple(bounds[1]),
        bounds_source=bounds_source,
        evidence_refs=[views[0]["ref_id"]],
        bounds_note="Blender rest-pose visual bounds; approximate and not checked against exported USD collision.",
    )


def _fmt(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("non-finite USD coordinate")
    return format(value, ".12g")


def _usd_identifier(asset_id: str) -> str:
    return "Asset_" + re.sub(r"[^A-Za-z0-9_]", "_", asset_id)


def _source_tree(asset: SceneAsset, output: Path) -> tuple[Path, Path, str]:
    supplied = asset.usd_path
    if supplied.is_symlink() or supplied.parent.is_symlink():
        raise ValueError("symlinked asset roots are not supported")
    source = supplied.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".usd", ".usda", ".usdc"}:
        raise ValueError(f"asset USD is missing or has an unsupported extension: {source}")
    with source.open("rb") as handle:
        header = handle.read(8)
    if not (header.startswith(b"PXR-USDC") or header.startswith(b"#usda")):
        raise ValueError(f"asset is not a USD file: {source}")
    if output.resolve().is_relative_to(source.parent):
        raise ValueError("output cannot be inside an asset export directory")
    for member in source.parent.rglob("*"):
        if member.is_symlink():
            raise ValueError(f"symlink in asset export directory: {member}")
    destination = output / "assets" / asset.asset_id
    shutil.copytree(source.parent, destination, symlinks=True)
    copied = destination / source.name
    if sha256(copied) != sha256(source):
        raise ValueError("copied USD differs from source")
    return source, copied, f"./assets/{asset.asset_id}/{source.name}"


def _provisional_pose(asset: SceneAsset, support: SupportSpec) -> dict:
    x0, y0, x1, y1 = asset.bbox_xyxy
    sx0, sy0, sx1, sy1 = support.image_bbox_xyxy
    # The visible bottom centre is a contact proxy. Its position is meaningful
    # only relative to the selected support image box and assumed view direction.
    anchor_x_px, anchor_y_px = (x0 + x1) / 2, y1
    u = (anchor_x_px - sx0) / (sx1 - sx0)
    v = (anchor_y_px - sy0) / (sy1 - sy0)
    x_m = (0.5 - v) * support.size_x_m
    y_m = (0.5 - u) * support.size_y_m
    z_m = support.top_z_m - asset.local_bounds_min_m[2] + support.clearance_m
    return {
        "position_world_m": [x_m, y_m, z_m],
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        "image_contact_proxy_px": [anchor_x_px, anchor_y_px],
        "support_uv": [u, v],
        "contact_projection_inside_support": 0 <= u <= 1 and 0 <= v <= 1,
        "placement_source": "estimated_from_image_bbox_bottom_center",
        "orientation_source": "assumed_identity",
        "scale_source": "authored_asset_metres_no_scene_scale",
        "camera_extrinsics": "unknown",
    }


def _write_usda(path: Path, support: SupportSpec, placed: list[dict]):
    top_center_z = support.top_z_m - support.thickness_m / 2
    body = [
        "#usda 1.0",
        "(",
        '    defaultPrim = "World"',
        "    metersPerUnit = 1",
        '    upAxis = "Z"',
        "    timeCodesPerSecond = 60",
        ")",
        "",
        'def PhysicsScene "physicsScene"',
        "{",
        "    vector3f physics:gravityDirection = (0, 0, -1)",
        "    float physics:gravityMagnitude = 9.81",
        "}",
        "",
        'def Xform "World"',
        "{",
        '    def Xform "Support"',
        "    {",
        '        def Material "Contact" (',
        '            prepend apiSchemas = ["PhysicsMaterialAPI"]',
        "        )",
        "        {",
        f"            float physics:staticFriction = {_fmt(support.static_friction)}",
        f"            float physics:dynamicFriction = {_fmt(support.dynamic_friction)}",
        f"            float physics:restitution = {_fmt(support.restitution)}",
        "        }",
        '        def Cube "Top" (',
        '            prepend apiSchemas = ["PhysicsCollisionAPI", "MaterialBindingAPI"]',
        "        )",
        "        {",
        "            rel material:binding:physics = </World/Support/Contact>",
        "            bool physics:collisionEnabled = 1",
        "            double size = 1",
        "            color3f[] primvars:displayColor = [(0.38, 0.38, 0.38)]",
        f"            double3 xformOp:translate = (0, 0, {_fmt(top_center_z)})",
        "            float3 xformOp:scale = "
        f"({_fmt(support.size_x_m)}, {_fmt(support.size_y_m)}, {_fmt(support.thickness_m)})",
        '            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]',
        "        }",
        "    }",
        '    def Xform "Assets"',
        "    {",
    ]
    for item in placed:
        x, y, z = item["pose"]["position_world_m"]
        name = item["prim_name"]
        reference = item["usd_ref"]
        body.extend(
            [
                f'        def Xform "{name}" (',
                f"            prepend references = @{reference}@",
            ]
        )
        if item["kind"] == "rigid":
            # Artisan currently tags even single rigid exports as articulation
            # roots. A stronger scene-layer list-op removes only that schema
            # from the referenced root; the source USD and physics bodies stay
            # untouched. Articulated exports retain their authored root API.
            body.append('            delete apiSchemas = ["PhysicsArticulationRootAPI"]')
        body.extend(
            [
                "        )",
                "        {",
                f"            double3 xformOp:translate = ({_fmt(x)}, {_fmt(y)}, {_fmt(z)})",
                '            uniform token[] xformOpOrder = ["xformOp:translate"]',
                "        }",
            ]
        )
    body.extend(["    }", "}", ""])
    path.write_text("\n".join(body), encoding="utf-8")


def assemble_scene(assets: list[SceneAsset], output: Path, support: SupportSpec) -> Path:
    """Create portable scene.usda, layout provenance, and copied USD dependencies.

    This is a composition result. A later Isaac reload and force/contact test
    must determine whether the authored physics actually works.
    """
    if not assets:
        raise ValueError("at least one asset is required")
    parsed = [
        SceneAsset.model_validate(asset.model_dump() if isinstance(asset, SceneAsset) else asset)
        for asset in assets
    ]
    support = SupportSpec.model_validate(
        support.model_dump() if isinstance(support, SupportSpec) else support
    )
    ids = [asset.asset_id for asset in parsed]
    if len(ids) != len(set(ids)) or len({_usd_identifier(name) for name in ids}) != len(ids):
        raise ValueError("asset IDs must be unique after USD path normalization")
    for asset in parsed:
        if asset.frame_id != support.frame_id or asset.image_size_xy != support.image_size_xy:
            raise ValueError("all placements must use the same image frame as the support")
    if output.exists():
        raise FileExistsError(output)

    with new_run(
        output,
        "provisional_scene_assembly",
        {
            "assets": [
                {
                    "asset_id": asset.asset_id,
                    "usd_path": str(asset.usd_path.resolve()),
                    "sha256": sha256(asset.usd_path),
                }
                for asset in parsed
            ]
        },
    ) as run:
        placed, issues = [], []
        for asset in parsed:
            source, copied, usd_ref = _source_tree(asset, output)
            pose = _provisional_pose(asset, support)
            if not pose["contact_projection_inside_support"]:
                issues.append(
                    {"code": "contact_projection_outside_support", "asset_id": asset.asset_id}
                )
            placed.append(
                {
                    "asset_id": asset.asset_id,
                    "kind": asset.kind,
                    "prim_name": _usd_identifier(asset.asset_id),
                    "prim_path": f"/World/Assets/{_usd_identifier(asset.asset_id)}",
                    "source_usd": str(source),
                    "source_sha256": sha256(source),
                    "usd_ref": usd_ref,
                    "copied_usd_sha256": sha256(copied),
                    "root_schema_adaptation": (
                        "scene_layer_deletes_PhysicsArticulationRootAPI"
                        if asset.kind == "rigid"
                        else "preserve_articulation_root_api"
                    ),
                    "frame_id": asset.frame_id,
                    "bbox_xyxy": list(asset.bbox_xyxy),
                    "evidence_refs": asset.evidence_refs,
                    "bounds_source": asset.bounds_source,
                    "bounds_note": asset.bounds_note,
                    "local_bounds_m": [
                        list(asset.local_bounds_min_m),
                        list(asset.local_bounds_max_m),
                    ],
                    "pose": pose,
                }
            )
        _write_usda(output / "scene.usda", support, placed)
        for item in placed:
            if not relative_file(output, item["usd_ref"].removeprefix("./")).is_file():
                raise ValueError(f"unresolved scene USD reference: {item['usd_ref']}")
        write_json(
            output / "layout.json",
            {
                "scene_ref": "scene.usda",
                "world_frame": "metres; Z-up; +X away from assumed viewer; +Y viewer-left",
                "image_mapping": "bbox bottom-center to support image bbox; affine proxy, not camera calibration",
                "orientation": "identity; yaw and surface perspective unresolved",
                "support": {
                    **support.model_dump(mode="json"),
                    "prim_path": "/World/Support/Top",
                    "physical_role": "static_collision_support",
                },
                "assets": placed,
                "issues": issues,
                "visual_validation": "not_run",
                "physics_validation": "not_run",
                "reload_validation": "not_run",
            },
        )
        run["validation"] = "composition_only"
        run["reconstruction_ready"] = False
        run["scene_ref"] = "scene.usda"
    return output
