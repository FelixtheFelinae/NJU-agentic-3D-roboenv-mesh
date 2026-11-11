"""Compatibility entry point; implementation: roboenv_perception.experiments.bicycle.isaac_drivetrain_worker."""

from importlib import import_module


def __getattr__(name):
    return getattr(import_module('roboenv_perception.experiments.bicycle.isaac_drivetrain_worker'), name)


if __name__ == "__main__":
    import runpy

    runpy.run_module('roboenv_perception.experiments.bicycle.isaac_drivetrain_worker', run_name="__main__")
