"""Compatibility entry point; implementation: roboenv_perception.experiments.g1.report."""

from importlib import import_module


def __getattr__(name):
    return getattr(import_module('roboenv_perception.experiments.g1.report'), name)


if __name__ == "__main__":
    import runpy

    runpy.run_module('roboenv_perception.experiments.g1.report', run_name="__main__")
