"""Shared semantic feature roles for preflight and analytic verification."""

import math

CYLINDRICAL = {"shaft", "hole", "cylinder"}
LINEAR = CYLINDRICAL | {"axis", "edge"}


def direction_residual(relation, kinds, axis_a, axis_b):
    """Unoriented angular residual, radians; plane directions denote normals."""
    norm = math.sqrt(sum(v * v for v in axis_a) * sum(v * v for v in axis_b))
    cosine = min(1.0, abs(sum(a * b for a, b in zip(axis_a, axis_b))) / norm)
    angle = math.acos(cosine)
    mixed = (kinds[0] == "plane") != (kinds[1] == "plane")
    relation_angle = math.pi / 2 - angle if mixed else angle
    return abs(math.pi / 2 - relation_angle) if relation == "orthogonal" else relation_angle


def relation_error(relation, kinds):
    """None means supported types, not that their geometry satisfies the relation."""
    if relation == "planar":
        return None if kinds == ["plane"] else "planar needs one plane"
    if relation in {"parallel", "orthogonal"}:
        if len(kinds) == 2 and all(k in LINEAR | {"plane"} for k in kinds):
            return None
        return "direction relation needs two planes, lines, or a line and plane"
    if relation == "coplanar":
        return None if kinds == ["plane", "plane"] else "coplanar needs two planes"
    if relation == "coaxial":
        if len(kinds) == 2 and all(k in LINEAR for k in kinds):
            return None
        return "coaxial needs two axes or edges; plane normals are not lines"
    if relation in {"clearance", "revolute", "cylindrical"}:
        if len(kinds) == 2 and all(k in CYLINDRICAL for k in kinds):
            return None
        return "fit needs two cylindrical surfaces; an ideal axis does not prove contact"
    return "unsupported relation"
