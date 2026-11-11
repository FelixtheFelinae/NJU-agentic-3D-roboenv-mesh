"""Reproject completed geometry into all video masks; separate fit and unused views."""

import argparse
from pathlib import Path

from roboenv_perception.construction.video_asset_workflow import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path('outputs/user-video-build-20261022'))
    args = parser.parse_args()
    report = evaluate(args.root, args.source)
    print({a: {k: v for k, v in r.items() if k != 'views'} for a, r in report['assets'].items()})


if __name__ == '__main__':
    main()
