import numpy as np
import pytest

from roboenv_perception.construction.simulation import TableSimulationSpec, tabletop_geometry


def plane_fixture():
    # A tilted SfM plane, deliberately in arbitrary units and away from the origin.
    u = np.array([1, 0, 0.0])
    v = np.array([0, 0.8, -0.6])
    n = np.cross(v, u)
    center = np.array([2, 3, 4.0])
    corners = [center-u*2-v, center+u*2-v, center+u*2+v, center-u*2+v]
    return {
        "u": u, "v": v, "normal": n, "width_sfm": 4, "depth_sfm": 2,
        "world_corners_sfm": np.asarray(corners).tolist(),
    }


def test_scale_alignment_and_collision_top_agree_without_claiming_measurement():
    plane = plane_fixture()
    spec = TableSimulationSpec(width_m=0.8, top_height_m=0.75)
    geometry = tabletop_geometry(plane, spec)
    transform = np.asarray(geometry["sfm_to_sim"])
    corners = np.column_stack([plane["world_corners_sfm"], np.ones(4)])
    world = (corners @ transform.T)[:, :3]
    np.testing.assert_allclose(world[:, 2], 0.75)
    np.testing.assert_allclose(np.ptp(world, axis=0), [0.4, 0.8, 0], atol=1e-10)
    assert np.linalg.det(transform[:3, :3]) > 0
    assert world[:, 0].min() == pytest.approx(spec.near_edge_x_m)
    assert geometry["scale_source"] == "assumed"
    assert geometry["height_source"] == "task_design"
    top = (geometry["position_world_m"][2]+geometry["collision_center_local_m"][2]
           +geometry["collision_size_m"][2]/2)
    assert top == pytest.approx(0.75)


def test_task_height_changes_placement_without_distorting_recovered_shape():
    a = tabletop_geometry(plane_fixture(), TableSimulationSpec(top_height_m=0.70))
    b = tabletop_geometry(plane_fixture(), TableSimulationSpec(top_height_m=0.80))
    np.testing.assert_array_equal(a["vertices_local_m"], b["vertices_local_m"])
    np.testing.assert_allclose(np.asarray(b["sfm_to_sim"])[:3, 3]-np.asarray(a["sfm_to_sim"])[:3, 3], [0, 0, 0.1])
    broken = plane_fixture()
    broken["normal"] = [1, 1, 1]
    with pytest.raises(ValueError, match="orthonormal"):
        tabletop_geometry(broken, TableSimulationSpec())


@pytest.mark.parametrize("options", [
    {"dynamic_friction": 0.8, "static_friction": 0.5},
    {"rest_offset_m": 0.005, "contact_offset_m": 0.002},
    {"thickness_m": 0.2, "top_height_m": 0.15},
])
def test_incoherent_contact_parameters_are_rejected(options):
    with pytest.raises(ValueError):
        TableSimulationSpec(**options)
