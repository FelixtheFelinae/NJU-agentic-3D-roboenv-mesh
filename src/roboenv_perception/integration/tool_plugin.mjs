/** EnvCreator tools for native DSH. No chat loop, robot controller or shell tool. */
import { spawn } from 'node:child_process';
import { randomUUID, createHash } from 'node:crypto';
import { readFile, mkdir, appendFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

export const name = 'envcreator-tools';
export const inject = ['tools'];
export const OPERATIONS = Object.freeze({
  inspect: 'Inspect the bound screwdriver asset, evidence and configured capabilities. Does not operate a robot.',
  research: 'Fetch the official manufacturer reference and preserve source, date and interface evidence. Dynamics remain explicit assumptions.',
  review: 'Verify existing ideal tool evidence against the unchanged asset, scene and current physics code. Reuses evidence; does not run a new simulation or prove robot success.',
  validate: 'Run the configured ideal tool fixture: alignment, hex engagement, loosening and five negative controls. Record before/after and actual physics evidence. Not a robot task.',
  build: 'Build a separate screwdriver candidate using existing reference-informed Blender/Artisan generation and optional functional validation. Does not replace the bound asset or live scene.',
  prepare_g1: 'After verified ideal feasibility, prepare a new G1 grasp/alignment scene bundle. Does not launch, attach or operate G1. Native motor and thread integration remain a separate step.',
});

/** Native DSH cancellation terminates the worker group; no automatic retry. */
export async function invokeBackend(config, operation, directory, signal) {
  signal?.throwIfAborted();
  return new Promise((done, reject) => {
    const child = spawn(config.python, ['-m', 'roboenv_perception.integration.tool_backend',
      '--config', config.configFile, '--operation', operation, '--output', directory],
    { shell: false, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '', cancelled = false, killTimer;
    const abort = () => {
      cancelled = true;
      if (child.pid) {
        try { process.kill(-child.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') reject(error); }
        killTimer = setTimeout(() => { try { process.kill(-child.pid, 'SIGKILL'); } catch {} }, 5000);
        killTimer.unref();
      }
    };
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    child.stdout.resume();
    child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-20000); });
    child.once('error', error => { signal?.removeEventListener('abort', abort); clearTimeout(killTimer); reject(error); });
    child.once('close', async (code, exitSignal) => {
      signal?.removeEventListener('abort', abort);
      if (!cancelled) clearTimeout(killTimer);
      else if (child.pid) {
        // The parent may exit before a simulator descendant; still stop its
        // owned process group instead of silently abandoning the descendants.
        try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') return reject(error); }
        clearTimeout(killTimer);
      }
      try {
        // The JSON result remains available even when feasibility did not pass.
        const result = await readFile(join(directory, 'result.json'), 'utf8').then(JSON.parse).catch(() => null);
        await mkdir(directory, { recursive: true });
        await writeFile(join(directory, 'worker.json'), JSON.stringify({ code, signal: exitSignal, cancelled, stderr }, null, 2));
        if (cancelled) throw new Error('EnvCreator operation cancelled; partial evidence retained');
        if (!result) throw new Error(`EnvCreator ${operation} exited ${code}; see ${directory}`);
        if (code !== 0 && !['unverified', 'failed'].includes(result.status)) throw new Error(`EnvCreator ${operation} has a result but exited ${code}; see ${directory}`);
        done({ ...result, worker_exit_code: code, evidence_directory: directory });
      } catch (error) { reject(error); }
    });
  });
}

export async function apply(ctx, options) {
  const configFile = resolve(options.configFile);
  const config = { ...JSON.parse(await readFile(configFile, 'utf8')), configFile };
  const directory = resolve(config.output_root);
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const journal = join(directory, 'plugin-events.jsonl');
  const record = value => appendFile(journal, JSON.stringify({ at: new Date().toISOString(), ...value }) + '\n', { mode: 0o600 });
  for (const [operation, description] of Object.entries(OPERATIONS)) {
    ctx.tools.register({ name: 'envcreator_' + operation, description,
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      output: { schema: { type: 'object', additionalProperties: true },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      timeoutMs: 1200000, isConcurrencySafe: () => false,
      execute: async (args, exec) => {
        if (args == null || Array.isArray(args) || Object.keys(args).length) throw new Error('This tool takes no arguments; paths and runtimes are deployment bindings');
        const call = { call_id: String(exec.callId), agent_id: exec.agent ? String(exec.agent.id) : null,
          operation, evidence_directory: join(directory, operation + '-' + randomUUID()) };
        await record({ event: 'before', ...call });
        try {
          const value = await invokeBackend(config, operation, call.evidence_directory, exec.signal);
          await record({ event: 'after', ...call, worker_exit_code: value.worker_exit_code,
            status: value.status ?? 'recorded', result_sha256: createHash('sha256').update(
              await readFile(join(call.evidence_directory, 'result.json'))).digest('hex') });
          return value;
        } catch (error) { await record({ event: 'failed', ...call, error: error.message }); throw error; }
      },
    });
  }
  await record({ event: 'loaded', tools: Object.keys(OPERATIONS).map(op => 'envcreator_' + op),
    robot_commands: false, model_loop: 'native DSH',
    plugin_sha256: createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex') });
}
