"""Scene handoff failure cases and real OpenUSD/native-launcher boundaries."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from roboenv_perception.integration.bundle import SCHEMA, digest, export_bundle, inspect_bundle
from roboenv_perception.integration.robot_scene import prepare_robot_scene


def bundle_fixture(root, status="unverified"):
    root.mkdir()
    (root / "scene").mkdir()
    (root / "metadata").mkdir()
    (root / "scene/scene.usda").write_text('#usda 1.0\n')
    (root / "metadata/delivery.json").write_text(json.dumps({"status": status, "scene_id": "fixture"}))
    files = {p.relative_to(root).as_posix(): digest(p) for p in root.rglob("*") if p.is_file()}
    manifest = {
        "schema": SCHEMA, "scene": {"path": "scene/scene.usda", "id": "fixture", "meters_per_unit": 1, "up_axis": "Z"},
        "delivery_status": status, "source_delivery_sha256": files["metadata/delivery.json"], "files_sha256": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def test_handoff_keeps_unverified_status(tmp_path):
    summary = inspect_bundle(bundle_fixture(tmp_path / "bundle"))
    assert summary["delivery_status"] == "unverified"
    assert summary["robot_execution"] == "not_run"


@pytest.mark.parametrize("damage", ["changed_usd", "extra_file", "escape", "symlink", "status", "units"])
def test_corrupt_bundle_is_rejected(tmp_path, damage):
    root = bundle_fixture(tmp_path / "bundle")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    if damage == "changed_usd":
        (root / "scene/scene.usda").write_text("modified")
    elif damage == "extra_file":
        (root / "scene/private.json").write_text("not a dependency")
    elif damage == "escape":
        manifest["files_sha256"]["../outside"] = "0" * 64
    elif damage == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("outside")
        (root / "scene/scene.usda").unlink()
        (root / "scene/scene.usda").symlink_to(outside)
    elif damage == "status":
        manifest["delivery_status"] = "verified"
    elif damage == "units":
        manifest["scene"]["meters_per_unit"] = 0.01
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        inspect_bundle(root)


@pytest.fixture
def usd_python():
    value = os.environ.get("ENVCREATOR_USD_PYTHON")
    if not value:
        pytest.skip("Set ENVCREATOR_USD_PYTHON to run the real separate OpenUSD worker")
    return Path(value)


def source_scene(root):
    root.mkdir()
    (root / "texture.png").write_bytes(b"referenced texture bytes")
    (root / "private.env").write_text("UNREFERENCED_DO_NOT_COPY=fixture")
    (root / "part.usda").write_text('''#usda 1.0
(
    defaultPrim = "Part"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "Part" (prepend apiSchemas = ["PhysicsRigidBodyAPI"])
{
    asset texture = @texture.png@
    def Cube "Shape" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {}
}
''')
    scene = root / "source.usda"
    scene.write_text('''#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "World"
{
    def Xform "Part" (prepend references = @part.usda@) {}
    def PhysicsFixedJoint "Joint"
    {
        rel physics:body1 = </World/Part>
    }
}
''')
    delivery = root / "delivery.json"
    delivery.write_text(json.dumps({"status": "unverified", "scene_id": "fixture", "scene_usd": str(scene)}))
    return delivery


@pytest.mark.parametrize("encoding", ["usda", "usdc"])
def test_real_usd_dependency_packaging_survives_relocation(tmp_path, usd_python, encoding):
    source = tmp_path / "source"
    delivery = source_scene(source)
    if encoding == "usdc":
        converted = subprocess.run([str(usd_python), "-c", "from pxr import Sdf; import sys; assert Sdf.Layer.FindOrOpen(sys.argv[1]).Export(sys.argv[2])", str(source / "part.usda"), str(source / "part.usdc")], capture_output=True, text=True)
        assert converted.returncode == 0, converted.stderr
        scene = source / "source.usda"
        scene.write_text(scene.read_text().replace("part.usda", "part.usdc"))
        (source / "part.usda").unlink()
    bundle = tmp_path / "bundle"
    exported = export_bundle(delivery, bundle, usd_python)
    assert exported["usd_check"]["rigid_bodies"] == 1
    assert exported["usd_check"]["joints"] == 1
    assert exported["delivery_status"] == "unverified"
    assert not list(bundle.rglob("private.env"))
    assert not list(bundle.rglob("*.usdc"))
    assert (bundle / "metadata/delivery.json").read_bytes() == delivery.read_bytes()
    relocated = tmp_path / "elsewhere" / "bundle"
    shutil.copytree(bundle, relocated)
    shutil.rmtree(source)
    shutil.rmtree(bundle)
    summary = inspect_bundle(relocated)
    probe = subprocess.run([str(usd_python), "-c", '''
import sys
from pathlib import Path
from pxr import Usd, UsdUtils, UsdPhysics
root=Path(sys.argv[1]).resolve()
layers,assets,missing=UsdUtils.ComputeAllDependencies(str(root/'scene.usda'))
assert not missing
assert all(Path(p).resolve().is_relative_to(root) for p in [l.realPath for l in layers]+list(assets))
stage=Usd.Stage.Open(str(root/'scene.usda'))
assert stage.GetPrimAtPath('/World/Part').HasAPI(UsdPhysics.RigidBodyAPI)
assert str(stage.GetPrimAtPath('/World/Joint').GetRelationship('physics:body1').GetTargets()[0])=='/World/Part'
''', str(relocated / "scene")], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert Path(summary["scene_usd"]).is_relative_to(relocated)


@pytest.mark.parametrize("damage", ["missing_texture", "invalid_joint", "wrong_units"])
def test_real_usd_failure_does_not_publish_bundle(tmp_path, usd_python, damage):
    source = tmp_path / "source"
    delivery = source_scene(source)
    if damage == "missing_texture":
        (source / "texture.png").unlink()
    else:
        scene = source / "source.usda"
        text = scene.read_text()
        scene.write_text(text.replace("</World/Part>", "</World/Missing>") if damage == "invalid_joint" else text.replace("metersPerUnit = 1", "metersPerUnit = 0.01"))
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="USD handoff failed"):
        export_bundle(delivery, output, usd_python)
    assert not output.exists()
    assert not list(tmp_path.glob(".envcreator-*"))


def test_delivery_without_scene_is_rejected_before_worker(tmp_path):
    delivery = tmp_path / "delivery.json"
    delivery.write_text(json.dumps({"status": "unverified", "scene_usd": None}))
    with pytest.raises(ValueError, match="no scene_usd"):
        export_bundle(delivery, tmp_path / "bundle", Path("missing-python"))


def test_robot_floor_keeps_source_geometry_and_joint_targets(tmp_path, usd_python):
    delivery = source_scene(tmp_path / "source")
    data = json.loads(delivery.read_text())
    data["status"] = "verified"
    delivery.write_text(json.dumps(data))
    original = export_bundle(delivery, tmp_path / "original", usd_python)
    derived = prepare_robot_scene(tmp_path / "original", tmp_path / "robot-test", usd_python, -0.2, (4, 5))
    assert derived["delivery_status"] == "unverified"
    assert inspect_bundle(tmp_path / "original")["manifest_sha256"] == original["manifest_sha256"]
    metadata = json.loads((tmp_path / "robot-test/bundle/metadata/delivery.json").read_text())
    assert metadata["upstream"]["delivery_status"] == "verified"
    probe = subprocess.run([str(usd_python), "-c", '''
import sys
from pxr import Usd,UsdGeom,UsdPhysics
original=Usd.Stage.Open(sys.argv[1]); derived=Usd.Stage.Open(sys.argv[2])
old=UsdGeom.XformCache().GetLocalToWorldTransform(original.GetPrimAtPath('/World/Part'))
new=UsdGeom.XformCache().GetLocalToWorldTransform(derived.GetPrimAtPath('/World/Environment/Part'))
assert old == new
floor=derived.GetPrimAtPath('/World/TestFloor')
assert floor.HasAPI(UsdPhysics.CollisionAPI)
assert not floor.HasAPI(UsdPhysics.RigidBodyAPI)
box=UsdGeom.BBoxCache(Usd.TimeCode.Default(),['default']).ComputeWorldBound(floor).ComputeAlignedRange()
assert abs(box.GetMax()[2] - (-.2)) < 1e-7
assert abs(box.GetMax()[0]-box.GetMin()[0]-4) < 1e-7
assert abs(box.GetMax()[1]-box.GetMin()[1]-5) < 1e-7
assert str(derived.GetPrimAtPath('/World/Environment/Joint').GetRelationship('physics:body1').GetTargets()[0]) == '/World/Environment/Part'
''', original["scene_usd"], derived["scene_usd"]], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr


def test_native_clawspine_parser_receives_checked_bundle(tmp_path):
    native_root = os.environ.get("ENVCREATOR_CLAWSPINE_ROOT")
    if not native_root:
        pytest.skip("Set ENVCREATOR_CLAWSPINE_ROOT for the actual native launcher boundary")
    bundle = bundle_fixture(tmp_path / "bundle")
    args = [sys.executable, "-m", "roboenv_perception.integration.cli", "clawspine",
            "--bundle", str(bundle), "--clawspine-root", native_root, "--dry-run",
            "--spawn", "1,0,0,0", "--observer-eye", "1,-4,2", "--observer-target", "1,0,1",
            "--model-provider", "fixture-provider", "--model", "fixture-model"]
    # Native accepts custom provider/model selection and binds these to DSH later.
    completed = subprocess.run(args, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["scene"]["usd"] == str(bundle / "scene/scene.usda")
    assert receipt["scene"]["spawn"] == "1,0,0,0"
    assert receipt["agent_mode"] == "native"
    assert receipt["control_mode"] == "whole_body"
    assert receipt["model_selection"]["model"] == "fixture-model"
    assert receipt["delivery_status"] == "unverified"
    assert receipt["native_execution"] == "not_run"
    corrupt = subprocess.run(args + ["--scene-usd", "/tmp/override.usd"], capture_output=True, text=True)
    assert corrupt.returncode != 0
    (bundle / "scene/scene.usda").write_text("changed after handoff")
    corrupt = subprocess.run(args, capture_output=True, text=True)
    assert corrupt.returncode != 0
    assert "hash mismatch" in corrupt.stderr
