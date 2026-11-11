/** Bind the user's local gateway to DSH's native pi-ai adapter. No model loop. */
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

export function loadModelConfig(path, python = process.env.ENVCREATOR_PYTHON ?? 'python3', selectedModel) {
  const code = `import json, sys
from pathlib import Path
from roboenv_perception.integration.dsh_model import read_model_config
try:
    result = read_model_config(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
except (ValueError, OSError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(2)
print(json.dumps(result))`;
  // stdout is private in-memory IPC, never forwarded to the terminal or a log.
  const result = spawnSync(python, ['-c', code, resolve(path), ...(selectedModel ? [selectedModel] : [])], { encoding: 'utf8', maxBuffer: 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(result.stderr?.trim() || 'Cannot read local DSH configuration');
  try { return JSON.parse(result.stdout); }
  catch { throw new Error('Cannot decode local DSH configuration'); }
}
