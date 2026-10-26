"""Run a trusted Blender mesh generator without starting the agent workflow."""

import argparse
import json
from pathlib import Path

from roboenv_perception.construction.blender_backend import build_blender_meshes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parameters", type=Path)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    result = build_blender_meshes(args.generator, args.output,
                                 parameters=json.loads(args.parameters.read_text()) if args.parameters else None,
                                 blender=args.blender, timeout=args.timeout, render=not args.no_render)
    print(json.dumps({"backend": result["backend"], "meshes": len(result["meshes"]),
                      "output": str(args.output.resolve()), "previews": result["previews"]}))


if __name__ == "__main__":
    main()
