"""Compatibility entry point; implementation: roboenv_perception.experiments.wood_table.isaac_scene_check."""

from importlib import import_module


def __getattr__(name):
    return getattr(import_module('roboenv_perception.experiments.wood_table.isaac_scene_check'), name)


if __name__ == "__main__":
    import runpy

    runpy.run_module('roboenv_perception.experiments.wood_table.isaac_scene_check', run_name="__main__")
