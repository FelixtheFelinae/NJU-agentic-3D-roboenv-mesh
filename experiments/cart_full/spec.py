"""Reviewed video-conditioned assembly recipe. All metric dimensions are assumed."""

import json
import math
from pathlib import Path

PITCH = 0.0127
PARTS = []
CONNECTIONS = []


def part(name, category, center, solids, group="chassis", status="observed_shape", refs=None):
    row = dict(
        id=name,
        category=category,
        center=center,
        solids=solids,
        body=group,
        evidence_status=status,
        evidence_refs=refs or ["video:4.0", "video:6.0"],
        dimension_source="assumed_metric_scale",
        mass_source="assumed",
    )
    PARTS.append(row)
    return row


def box(center, size, color="metal"):
    return dict(shape="box", center=center, size=size, color=color)


def cylinder(center, radius, length, axis=(0, 0, 1), color="metal", sides=32):
    return dict(
        shape="cylinder",
        center=center,
        radius=radius,
        length=length,
        axis=axis,
        color=color,
        sides=sides,
    )


def perforated_plate(length, width, thickness, rows=3, pitch=PITCH):
    # Plate assembled from abutting strips: real through holes, no filled convex hull.
    nx = int((length - 0.006) / pitch)
    xs = [(i - (nx - 1) / 2) * pitch for i in range(nx)]
    ys = [(j - (rows - 1) / 2) * pitch for j in range(rows)]
    hole = 0.0054
    out = []
    ylo = -width / 2
    for y in ys:
        if y - hole / 2 > ylo:
            out.append(
                box([0, (ylo + y - hole / 2) / 2, 0], [length, y - hole / 2 - ylo, thickness])
            )
        xlo = -length / 2
        for x in xs:
            if x - hole / 2 > xlo:
                out.append(
                    box([(xlo + x - hole / 2) / 2, y, 0], [x - hole / 2 - xlo, hole, thickness])
                )
            xlo = x + hole / 2
        if length / 2 > xlo:
            out.append(box([(xlo + length / 2) / 2, y, 0], [length / 2 - xlo, hole, thickness]))
        ylo = y + hole / 2
    if width / 2 > ylo:
        out.append(box([0, (ylo + width / 2) / 2, 0], [length, width / 2 - ylo, thickness]))
    return out


def channel(name, center, length, width=0.0381, depth=0.0254, along="x", group="chassis"):
    solids = perforated_plate(length, width, 0.0016, 3)
    for side in [-1, 1]:
        for s in perforated_plate(length, depth, 0.0016, 1):
            x, y, z = s["center"]
            a, b, c = s["size"]
            s["center"] = [x, side * (width / 2 - 0.0008), y - depth / 2]
            s["size"] = [a, c, b]
            solids.append(s)
    if along == "y":
        for s in solids:
            x, y, z = s["center"]
            a, b, c = s["size"]
            s["center"] = [-y, x, z]
            s["size"] = [b, a, c]
    return part(name, "perforated_channel", center, solids, group)


def screw(name, center, clamped, mate, observed=False, target=False):
    solids = [
        cylinder([0, 0, 0], 0.0036, 0.0028, color="target" if target else "steel"),
        cylinder([0, 0, -0.0054], 0.0019, 0.008, color="steel"),
        cylinder([0, 0, 0.00142], 0.00135, 0.00008, color="dark", sides=6),
    ]
    part(
        name,
        "fastener",
        center,
        solids,
        "chassis",
        "observed_head" if observed else "assumed_fastener",
        ["video:18.9"] if target else ["video:4.0", "video:6.0"],
    )
    CONNECTIONS.append(
        dict(
            id="conn_" + name,
            fastener=name,
            clamped_parts=clamped,
            mate=mate,
            type="threaded_hypothesis",
            axis=[0, 0, 1],
            head_interface="hex_socket_assumed",
            hidden_mate="unknown: tapped support or captive nut",
            thread_spec="unknown",
            source="visible_head_with_assumed_hidden_connection"
            if observed
            else "assembly_hypothesis",
            executable_model="local_screw_surrogate" if target else "not_compiled",
        )
    )


def build():
    PARTS.clear()
    CONNECTIONS.clear()
    for side, x in [("left", -0.126), ("right", 0.126)]:
        channel("rail_" + side, [x, 0, 0.091], 0.3175, along="y")
    channel("cross_rear", [0, -0.092, 0.0926], 0.2794)
    channel("cross_front", [0, 0.104, 0.0926], 0.2794)
    channel("cross_upper", [0, 0.104, 0.112], 0.2286, width=0.0381, depth=0.0127)
    channel("bumper", [0, 0.226, 0.078], 0.3556, depth=0.0254)
    for side, x in [("left", -0.09), ("right", 0.09)]:
        part(
            "upper_spacer_" + side,
            "mount_spacer",
            [x, 0.104, 0.102],
            [box([0, 0, 0], [0.015, 0.025, 0.018])],
            status="assumed_mount",
        )
    for side, x in [("left", -0.1016), ("right", 0.1016)]:
        part(
            "bumper_standoff_" + side,
            "standoff",
            [x, 0.174, 0.073],
            [cylinder([0, 0, 0], 0.0032, 0.093, axis=[0, 1, 0])],
        )
        part(
            "bumper_mount_" + side,
            "bracket",
            [x, 0.127, 0.079],
            [box([0, 0, 0], [0.018, 0.024, 0.002]), box([0, 0.01, -0.007], [0.018, 0.002, 0.014])],
            status="inferred_geometry",
        )
        part(
            "bumper_pad_" + side,
            "black_end_block",
            [x, 0.226, 0.055],
            [
                box([-0.0101, 0, 0], [0.0148, 0.027, 0.045], "dark"),
                box([0.0101, 0, 0], [0.0148, 0.027, 0.045], "dark"),
                box([0, -0.0081, 0], [0.0054, 0.0108, 0.045], "dark"),
                box([0, 0.0081, 0], [0.0054, 0.0108, 0.045], "dark"),
            ],
            status="observed_envelope_unknown_function",
        )
        part(
            "bumper_receiver_" + side,
            "thread_receiver",
            [x, 0.226, 0.0735],
            [
                box([-0.00435, 0, 0], [0.0033, 0.012, 0.008]),
                box([0.00435, 0, 0], [0.0033, 0.012, 0.008]),
                box([0, -0.00435, 0], [0.0054, 0.0033, 0.008]),
                box([0, 0.00435, 0], [0.0054, 0.0033, 0.008]),
            ],
            status="assumed_hidden_thread_receiver",
        )
        screw(
            "bumper_screw_" + side,
            [x, 0.226, 0.0803],
            ["bumper"],
            "bumper_receiver_" + side,
            True,
            side == "right",
        )
        screw(
            "mount_screw_" + side,
            [x, 0.127, 0.0949],
            ["bumper_mount_" + side, "cross_front"],
            "rail_" + side,
        )
    for end, y in [("front", 0.104), ("rear", -0.105)]:
        for side, x in [("left", -0.160), ("right", 0.160)]:
            name = f"wheel_{end}_{side}"
            solids = []
            if end == "front":
                solids.append(cylinder([0, 0, 0], 0.043, 0.022, axis=[1, 0, 0], color="dark"))
                for j in range(16):
                    a = j * 2 * math.pi / 16
                    solids.append(
                        cylinder(
                            [0, 0.045 * math.sin(a), 0.045 * math.cos(a)],
                            0.006,
                            0.013,
                            axis=[0, math.cos(a), -math.sin(a)],
                            color="rubber",
                            sides=12,
                        )
                    )
            else:
                solids.append(
                    dict(
                        shape="torus",
                        center=[0, 0, 0],
                        radius=0.044,
                        minor=0.006,
                        axis=[1, 0, 0],
                        color="rubber",
                    )
                )
                solids.append(cylinder([0, 0, 0], 0.011, 0.023, axis=[1, 0, 0], color="dark"))
                for j in range(10):
                    a = j * 2 * math.pi / 10
                    solids.append(
                        dict(
                            shape="rod",
                            start=[0, 0.009 * math.sin(a), 0.009 * math.cos(a)],
                            end=[0, 0.043 * math.sin(a), 0.043 * math.cos(a)],
                            radius=0.0025,
                            color="dark",
                        )
                    )
            solids.append(cylinder([0, 0, 0], 0.007, 0.027, axis=[1, 0, 0], color="steel"))
            part(
                name,
                "roller_wheel" if end == "front" else "traction_wheel",
                [x, y, 0.051],
                solids,
                name,
            )
            sign = -1 if x < 0 else 1
            part(
                "axle_" + end + "_" + side,
                "square_axle",
                [x - sign * 0.008, y, 0.051],
                [box([0, 0, 0], [0.076, 0.0032, 0.0032], "steel")],
                name,
                "observed_shaft_inferred_length",
                ["video:17.1"],
            )
            part(
                "bearing_" + end + "_" + side,
                "bearing_mount",
                [sign * 0.126, y, 0.051],
                [
                    box([0, 0, 0], [0.008, 0.023, 0.032], "dark"),
                    box([0, 0, 0.022], [0.008, 0.023, 0.037], "dark"),
                    cylinder([sign * 0.005, 0, 0], 0.006, 0.004, [1, 0, 0], "steel"),
                ],
                status="assumed_mount",
            )
            # Modules seen inside both sides; identity and drive assignment remain hypotheses.
            part(
                "module_" + end + "_" + side,
                "black_module",
                [sign * 0.087, y, 0.057],
                [
                    box([0, 0, 0], [0.058, 0.067, 0.036], "dark"),
                    *[
                        box([k * 0.004, 0, 0.0182], [0.0016, 0.036, 0.0005], "black")
                        for k in range(-5, 6)
                    ],
                ],
                status="observed_envelope_unknown_model",
            )
            CONNECTIONS.append(
                dict(
                    id="spin_" + name,
                    type="revolute",
                    parent="chassis",
                    child=name,
                    origin=[x, y, 0.051],
                    axis=[1, 0, 0],
                    source="inferred_rolling_axis",
                    transmission="unresolved",
                )
            )
    part(
        "controller",
        "screen_controller",
        [0, -0.008, 0.099],
        [
            box([0, 0, 0], [0.145, 0.116, 0.028], "housing"),
            box([0, 0.004, 0.0144], [0.112, 0.082, 0.001], "screen"),
            cylinder([-0.059, -0.042, 0.015], 0.005, 0.001, color="black"),
        ],
    )
    part(
        "power_box",
        "black_box_adjacent_controller",
        [0, -0.080, 0.108],
        [box([0, 0, 0], [0.12, 0.030, 0.022], "dark")],
        status="observed_envelope_unknown_model",
    )
    for side, x in [("left", -0.016), ("right", 0.016)]:
        part(
            "yellow_block_" + side,
            "yellow_block",
            [x, 0.104, 0.125],
            [
                box([0, 0, 0], [0.022, 0.022, 0.026], "yellow"),
                cylinder([0, 0, 0.0132], 0.003, 0.0003, color="dark"),
            ],
            status="observed_unknown_function",
        )
    for end, y in [("front", 0.104), ("rear", -0.092)]:
        for side, x in [("left", -0.126), ("right", 0.126)]:
            screw(
                "corner_" + end + "_" + side, [x, y, 0.095], ["cross_" + end], "rail_" + side, True
            )
    # No invented complete motor/brain screw pattern. Hidden fasteners stay unknown.
    for side, x in [("left", -0.105), ("right", 0.105)]:
        solids = []
        pts = [
            [x, -0.055, 0.087],
            [x, -0.02, 0.085],
            [x * 0.7, 0.01, 0.085],
            [x * 0.7, 0.047, 0.09],
            [x, 0.07, 0.08],
        ]
        for a, b in zip(pts, pts[1:]):
            solids.append(dict(shape="rod", start=a, end=b, radius=0.0018, color="black"))
        part(
            "cable_" + side,
            "cable_envelope",
            [0, 0, 0],
            solids,
            status="illustrative_route_not_identified",
        )
    return dict(
        schema="cart-full/1",
        axes="X lateral; +Y bumper; Z up",
        parts=PARTS,
        connections=CONNECTIONS,
        scale=dict(
            source="assumed",
            pitch_m=PITCH,
            wheel_diameter_m=0.1016,
            warning="Not measured or matched to manufacturer CAD; proportions estimated from video",
        ),
        unknowns=[
            "bottom frame and hidden fasteners",
            "backside nuts",
            "wheel retention",
            "gear trains",
            "exact motor/module identities",
            "cable endpoints",
            "materials and mass",
        ],
        review_corrections=[
            "VLM front/rear wheel descriptions rejected: roller wheels are bumper-side",
            "knobby tire interpretation rejected: grey peripheral elements are rollers",
            "electrical function of yellow blocks remains unknown",
            "no inference of four hidden motor bolts or countersunk screen screws",
        ],
        collision_policy="full-car dynamics uses declared conservative body proxies; tool check uses detailed structural solids",
        wheel_roller_dynamics="rollers are visual only and fused to wheel for first rolling test; no omnidirectional contact claim",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build(), indent=2) + "\n")
