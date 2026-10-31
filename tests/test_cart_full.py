import copy

from experiments.cart_full.spec import build
from experiments.cart_full.validate import audit_spec


def test_model_has_resolvable_connections_and_open_screw_path():
    result = audit_spec(build())
    assert all(result["checks"].values()), result


def test_filled_hidden_pad_is_rejected():
    spec = copy.deepcopy(build())
    pad = next(p for p in spec["parts"] if p["id"] == "bumper_pad_right")
    pad["solids"] = [
        {"shape": "box", "center": [0, 0, 0], "size": [0.035, 0.027, 0.045], "color": "dark"}
    ]
    result = audit_spec(spec)
    assert not result["checks"]["initial_screw_shank_clearance"]
    assert "bumper_pad_right:0" in result["initial_shank_collision_ids"]


def test_missing_mate_is_rejected():
    spec = copy.deepcopy(build())
    spec["connections"][0]["mate"] = "nonexistent"
    assert not audit_spec(spec)["checks"]["connection_references_exist"]
