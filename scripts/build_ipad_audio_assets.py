"""Compatibility entry point; implementation: roboenv_perception.experiments.white_table.build_assets."""

from importlib import import_module


def __getattr__(name):
    return getattr(import_module('roboenv_perception.experiments.white_table.build_assets'), name)


if __name__ == "__main__":
    import runpy

    runpy.run_module('roboenv_perception.experiments.white_table.build_assets', run_name="__main__")
