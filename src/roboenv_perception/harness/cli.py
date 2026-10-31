"""Command line entry for video/image -> construction -> review -> delivery."""

import argparse
import json
from pathlib import Path

from .run import HarnessRequest, HarnessSettings, run_harness


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Object and desired interaction in one sentence")
    media = parser.add_mutually_exclusive_group(required=True)
    media.add_argument("--video", type=Path)
    media.add_argument("--images", nargs="+", type=Path)
    parser.add_argument("--scene-id", default="scene")
    parser.add_argument("--perception-config", type=Path, required=True)
    parser.add_argument("--construction-config", type=Path, required=True)
    parser.add_argument("--artisan-python", type=Path, required=True)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--isaac-python", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, help="Explicit local API credential file")
    parser.add_argument("--max-revisions", type=int, default=3)
    parser.add_argument("--max-physics-revisions", type=int, default=2)
    parser.add_argument("--max-wall-seconds", type=int, default=7200)
    parser.add_argument("--web-provider", choices=["duckduckgo", "wikipedia", "searxng"], default="duckduckgo")
    parser.add_argument("--web-dns-resolver", choices=["system", "google"], default="system")
    parser.add_argument("--searxng-endpoint", default="")
    parser.add_argument("--official-domain", action="append", default=[])
    parser.add_argument("--allowed-source-domain", action="append", default=[])
    parser.add_argument("--allow-category-search", action="store_true")
    parser.add_argument("--no-web-search", action="store_true")
    parser.add_argument("--no-physics", action="store_true")
    parser.add_argument("--no-scene", action="store_true")
    parser.add_argument("--background-mode", choices=["off", "masks", "auto"], default="auto")
    args = parser.parse_args(argv)
    if args.env_file:
        from ..secret_env import load_secret_env

        load_secret_env(args.env_file)
    request = HarnessRequest(scene_id=args.scene_id, task=args.task, video=args.video,
                             images=args.images or [])
    settings = HarnessSettings(perception_config=args.perception_config,
                               construction_config=args.construction_config,
                               artisan_python=args.artisan_python,
                               blender=args.blender, isaac_python=args.isaac_python,
                               max_geometry_revisions=args.max_revisions,
                               max_physics_revisions=args.max_physics_revisions,
                               max_wall_seconds=args.max_wall_seconds,
                               web_search=not args.no_web_search,
                               web_provider=args.web_provider,
                               web_dns_resolver=args.web_dns_resolver,
                               searxng_endpoint=args.searxng_endpoint,
                               official_domains=args.official_domain,
                               allowed_source_domains=args.allowed_source_domain,
                               allow_category_search=args.allow_category_search,
                               enable_scene_assembly=not args.no_scene,
                               enable_physics=not args.no_physics,
                               background_mode=args.background_mode)
    result = run_harness(request, settings, args.output)
    print(json.dumps({"status": result["status"], "delivery": result["delivery_path"],
                      "scene_usd": result["scene_usd"]}, ensure_ascii=False))
    return 0 if result["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
