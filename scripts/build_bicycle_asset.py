"""Compatibility entry point; implementation: roboenv_perception.experiments.bicycle.build_asset."""

from importlib import import_module


def __getattr__(name):
    return getattr(import_module('roboenv_perception.experiments.bicycle.build_asset'), name)


if __name__ == "__main__":
    import runpy

    runpy.run_module('roboenv_perception.experiments.bicycle.build_asset', run_name="__main__")
