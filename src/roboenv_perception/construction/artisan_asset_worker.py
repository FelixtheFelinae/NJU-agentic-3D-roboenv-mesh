"""Executed only by the separate Artisan runtime, without provider credentials."""

import dataclasses
import json
import sys
from pathlib import Path


def main():
    from sdk.runtime import runtime

    mode, directory = sys.argv[1:]
    workspace = Path(directory)
    model = workspace/'model.py'
    if mode == 'union':
        import trimesh

        meshes = []
        for path in (workspace/'inputs').glob('*.obj'):
            mesh = trimesh.load(path, force='mesh')
            parts = list(mesh.split())
            if len(parts) > 1:
                mesh = trimesh.boolean.union(parts, engine='manifold')
                mesh.export(path)
            meshes.append({'name': path.name, 'watertight': bool(mesh.is_watertight),
                           'volume_m3': float(mesh.volume), 'source_components': len(parts)})
        result = {'passed': bool(meshes) and all(m['watertight'] and m['volume_m3'] > 0 for m in meshes),
                  'meshes': meshes}
    elif mode == 'qa':
        result = runtime.run_qa_tests(model, (workspace/'qa_tests.py').read_text())
        result['passed'] = bool(result.get('ok')) and bool(result.get('passed', True))
    else:
        from sdk.runtime.render import export

        outcome, display = runtime.compile_for_agent(model, workspace/'compile-report.json')
        result = {'passed': outcome.status == 'pass', 'status': outcome.status, 'display': display}
        # The SDK uses "ok" for a successful compile on some revisions.
        if outcome.status in {'ok', 'success', 'passed'}:
            result['passed'] = True
        if dataclasses.is_dataclass(outcome):
            result['outcome'] = dataclasses.asdict(outcome)
        if result['passed']:
            result['export_report'] = export(model, ['urdf', 'mjcf', 'usd'], workspace/'export')
            result['exports'] = result['export_report']['exports']
            result['passed'] = all(k in result['exports'] for k in ['urdf', 'mjcf', 'usd'])
    (workspace/f'{mode}.json').write_text(json.dumps(result, indent=2, default=str)+'\n')
    print(json.dumps(result, default=str))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
