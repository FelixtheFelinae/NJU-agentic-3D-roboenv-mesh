"""Keep the existing PhysX workers explicit across Isaac Lab's 2.x/3.x APIs.

Call these helpers after starting the simulator. They do not launch an app or
change the solver, time step, contact thresholds, or asset parameters.
"""

from importlib import import_module, metadata


def runtime_versions():
    result = {}
    for name in ("isaaclab", "isaacsim", "torch", "numpy", "mujoco", "newton"):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def _lab_major():
    return int(metadata.version("isaaclab").split(".", 1)[0])


def physx_config(**kwargs):
    module = "isaaclab_physx.physics" if _lab_major() >= 3 else "isaaclab.sim"
    return import_module(module).PhysxCfg(**kwargs)


def simulation_config(*, physx=None, **kwargs):
    """Preserve PhysX and legacy explicit-actuator execution for existing probes."""
    sim = import_module("isaaclab.sim")
    if _lab_major() >= 3:
        if "physics" in kwargs:
            raise ValueError("Existing PhysX workers cannot select another physics backend")
        kwargs["physics"] = physx if physx is not None else physx_config()
        kwargs.setdefault("use_newton_actuators", False)
    elif physx is not None:
        kwargs["physx"] = physx
    return sim.SimulationCfg(**kwargs)


def runtime_quaternion(wxyz):
    """Convert existing asset/recipe WXYZ values to the installed Lab convention."""
    w, x, y, z = wxyz
    return (x, y, z, w) if _lab_major() >= 3 else (w, x, y, z)


def quaternion_wxyz(value):
    """Keep existing report and mechanical-math quaternion contracts in WXYZ."""
    a, b, c, d = map(float, value)
    return [d, a, b, c] if _lab_major() >= 3 else [a, b, c, d]


def state_wxyz(state):
    """Copy a NumPy body/root state; normalize only its quaternion for consumers.

    Raw simulator state clones used for reset must remain in Lab's convention.
    """
    result = state.copy()
    if _lab_major() >= 3:
        result[..., 3:7] = state[..., [6, 3, 4, 5]]
    return result


def close_simulation(sim):
    """Use Lab 3's consolidated teardown while retaining the older callback path."""
    sim.stop()
    clear_callbacks = getattr(sim, "clear_all_callbacks", None)
    if clear_callbacks is not None:
        clear_callbacks()
    sim.clear_instance()
