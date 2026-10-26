import json
import os
import shutil

import numpy as np
import pytest

from roboenv_perception.construction.blender_backend import (
    build_blender_meshes,
    resolve_blender,
    validate_result,
)


@pytest.fixture
def blender():
    candidate = os.environ.get("BLENDER_BIN") or shutil.which("blender")
    if not candidate:
        pytest.skip("Optional real Blender integration: set BLENDER_BIN")
    return resolve_blender(candidate)


def test_missing_blender_has_actionable_error(monkeypatch):
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="BLENDER_BIN"):
        resolve_blender()


def test_blender_roundtrip_preserves_metric_frame_and_detects_changed_mesh(tmp_path, blender):
    generator = tmp_path / "box.py"
    generator.write_text("""import bpy
bpy.ops.mesh.primitive_cube_add(size=1, location=(0.12, -0.08, 0.03))
obj = bpy.context.object
obj.scale = (0.10, 0.20, 0.04)
export_mesh('body', obj)
""")
    output = tmp_path / "run"
    report = build_blender_meshes(generator, output, blender=blender, render=False)
    mesh = report["meshes"][0]
    np.testing.assert_allclose(
        mesh["bounds_m"], [[0.07, -0.18, 0.01], [0.17, 0.02, 0.05]], atol=1e-7
    )
    assert mesh["volume_m3"] == pytest.approx(0.0008, rel=1e-6)
    assert report["previews"] == []
    assert (output / "asset.blend").stat().st_size > 1000
    with pytest.raises(FileExistsError):
        build_blender_meshes(generator, output, blender=blender, render=False)
    with (output / mesh["file"]).open("a") as stream:
        stream.write("# changed after validation\n")
    with pytest.raises(ValueError, match="changed"):
        validate_result(output)


def test_blender_rejects_open_surface_and_preserves_failure(tmp_path, blender):
    generator = tmp_path / "plane.py"
    generator.write_text(
        "import bpy\nbpy.ops.mesh.primitive_plane_add()\nexport_mesh('body', bpy.context.object)\n"
    )
    output = tmp_path / "run"
    with pytest.raises(RuntimeError, match="Blender exited"):
        build_blender_meshes(generator, output, blender=blender, render=False)
    assert not (output / "result.json").exists()
    assert (output / "blender.log").is_file()
    assert json.loads((output / "failure.json").read_text())


def test_articulated_mesh_keeps_local_vertices_and_assembled_bounds(tmp_path, blender):
    generator = tmp_path / "part.py"
    generator.write_text("""import bpy
bpy.ops.mesh.primitive_cube_add(size=0.1, location=(0, 0, 0))
export_mesh('wheel', bpy.context.object, part='front_wheel',
            preview_translation=(0.54, 0, 0.30))
""")
    report = build_blender_meshes(generator, tmp_path / "run", blender=blender, render=False)
    mesh = report["meshes"][0]
    assert mesh["part"] == "front_wheel"
    np.testing.assert_allclose(
        mesh["bounds_m"], [[-0.05, -0.05, -0.05], [0.05, 0.05, 0.05]], atol=1e-7
    )
    np.testing.assert_allclose(
        report["bounds_m"], [[0.49, -0.05, 0.25], [0.59, 0.05, 0.35]], atol=1e-7
    )
