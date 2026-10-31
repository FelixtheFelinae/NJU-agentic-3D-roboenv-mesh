#!/usr/bin/env node
/** Submit one asset-development goal through ClawSpine's native DSH transport.
 * DSH owns model execution and tool dispatch. This runner observes and closes
 * its isolated DSH host; it has no robot control or custom model loop.
 */
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, open } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { parseArgs } from 'node:util';
import { loadModelConfig } from '../../src/roboenv_perception/integration/model_config.mjs';

const { values } = parseArgs({ options: {
  'clawspine-root': { type: 'string' }, 'plugin-patch': { type: 'string' },
  'provider-patch': { type: 'string' }, 'task-file': { type: 'string' },
  'model-config': { type: 'string' },
  output: { type: 'string' }, provider: { type: 'string' }, model: { type: 'string' },
  port: { type: 'string', default: '8897' }, 'wall-seconds': { type: 'string', default: '420' },
} });
for (const key of ['clawspine-root', 'plugin-patch', 'task-file', 'output']) {
  if (!values[key]) throw new Error('Missing --' + key);
}
if (values['model-config'] && ['provider-patch', 'provider', 'model'].some(key => values[key])) throw new Error('Use model-config or explicit provider arguments');
if (!values['model-config'] && ['provider-patch', 'provider', 'model'].some(key => !values[key])) throw new Error('Independent model configuration is required');
const modelConfig = values['model-config'] ? loadModelConfig(values['model-config']) : null;
if (modelConfig) { values.provider = modelConfig.summary.provider; values.model = modelConfig.summary.model; }
const root = resolve(values['clawspine-root']), output = resolve(values.output);
const wallMs = Number(values['wall-seconds']) * 1000;
if (!Number.isFinite(wallMs) || wallMs <= 0) throw new Error('Invalid wall deadline');
const moduleAt = relative => import(pathToFileURL(join(root, relative)));
const { installedDshCommand } = await moduleAt('scripts/launch_dsh_sim.mjs');
const { connectDshWeb, dshWebUrlFromOutput } = await moduleAt('scripts/dsh/web-auth.mjs');
const { nativeRpc } = await moduleAt('scripts/rsi/native-session.mjs');
const { connectArenaDsh } = await moduleAt('scripts/arena/dsh-client.mjs');
await mkdir(output, { mode: 0o700 });
if (modelConfig) {
  values['provider-patch'] = join(output, 'independent-provider.patch.yml');
  await writeFile(values['provider-patch'], modelConfig.patch, { flag: 'wx', mode: 0o600 });
}
const workspace = join(output, 'workspace');
await mkdir(workspace, { mode: 0o700 });
const save = (name, value) => writeFile(join(output, name), JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
const task = (await readFile(resolve(values['task-file']), 'utf8')).trim();
const inputs = {};
for (const key of ['plugin-patch', 'provider-patch', 'task-file']) {
  inputs[key] = { path: resolve(values[key]), sha256: createHash('sha256').update(await readFile(resolve(values[key]))).digest('hex') };
}
const result = { scope: 'native-dsh-asset-development-preflight', task, inputs,
  requested_model: { provider: values.provider, model: values.model },
  ...(modelConfig ? { independent_model_config: modelConfig.summary } : {}),
  robot_execution: 'not_run', robot_task_submitted: false, model_execution: 'not_confirmed',
  started_at: new Date().toISOString(), session_id: randomUUID(), request_id: randomUUID(),
  dispatched: false, turn_completed: false, errors: [] };
await save('definition.json', result);
const cli = installedDshCommand(['--profile', 'envcreator', '--from-default-profile', 'web',
  '--patch', resolve(values['plugin-patch']), '--patch', resolve(values['provider-patch']),
  '--port', values.port, '--no-open']);
const log = await open(join(output, 'dsh-private.log'), 'wx', 0o600);
const child = spawn(cli.executable, cli.args, { cwd: workspace,
  env: { ...Object.fromEntries(Object.entries(process.env).filter(([name]) => !/(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)/i.test(name))),
    ...modelConfig?.environment, DSH_HOME: join(output, 'dsh-home') }, stdio: ['ignore', log.fd, log.fd] });
const exited = new Promise(done => child.once('exit', (code, signal) => done({ code, signal })));
let client, snapshot;
try {
  const startDeadline = Date.now() + 60000;
  let launchUrl;
  while (Date.now() < startDeadline) {
    launchUrl = dshWebUrlFromOutput(await readFile(join(output, 'dsh-private.log'), 'utf8'));
    if (launchUrl) break;
    if (child.exitCode !== null) throw new Error('Native DSH exited during startup');
    await delay(500);
  }
  if (!launchUrl) throw new Error('Native DSH startup timed out');
  const connection = await connectDshWeb(launchUrl);
  await connection.response.body?.cancel();
  const created = await nativeRpc(connection, 'session/create', { args: { request: {
    cwd: workspace, sessionId: result.session_id, agentPreset: 'envcreator-asset',
  } } }, AbortSignal.timeout(30000));
  const selection = await nativeRpc(connection, 'session/selectModel', { args: { request: {
    sessionId: created.sessionId, provider: values.provider, model: values.model,
  } } }, AbortSignal.timeout(30000));
  result.selected_model = selection.selected;
  client = await connectArenaDsh(launchUrl);
  await save('before-native.json', await client.snapshot(result.session_id, { maxMessages: 5000 }));
  await save('dispatch-attempt.json', result);
  result.acceptance = await client.prompt(result.session_id, task, { requestId: result.request_id });
  result.dispatched = true;
  const deadline = Date.now() + wallMs;
  while (Date.now() < deadline) {
    snapshot = await client.snapshot(result.session_id, { maxMessages: 10000 });
    await save('native-trajectory.json', snapshot);
    const events = snapshot.records.map(row => row.event).filter(Boolean);
    const user = events.find(event => event.type === 'user/message' && event.data?.source?.rpcId === result.request_id);
    const end = user && events.find(event => event.type === 'turn/end' && event.seq > user.seq);
    if (end) {
      result.turn_completed = true;
      result.native_end = end;
      if (end.data?.reason?.kind === 'error') throw new Error('Native DSH model request failed: ' + (end.data.reason.error?.message ?? 'provider error'));
      break;
    }
    await delay(2000);
  }
  if (!result.turn_completed) throw new Error('Native DSH turn exceeded preflight deadline');
} catch (error) {
  result.errors.push(error.message);
} finally {
  if (result.dispatched) {
    try { result.cancel = await client.cancel(result.session_id); } catch (error) { result.errors.push('cancel: ' + error.message); }
    try { snapshot = await client.snapshot(result.session_id, { maxMessages: 10000 }); } catch (error) { result.errors.push('snapshot: ' + error.message); }
  }
  if (snapshot) {
    await save('native-trajectory.json', snapshot);
    result.event_types = {};
    for (const row of snapshot.records) {
      const type = row.event?.type;
      if (type) result.event_types[type] = (result.event_types[type] ?? 0) + 1;
    }
    if (snapshot.records.some(row => row.event?.type === 'assistant/message')) result.model_execution = 'native_response_received';
  }
  client?.close();
  if (child.exitCode === null) child.kill('SIGTERM');
  let stopped = await Promise.race([exited, delay(15000).then(() => null)]);
  if (!stopped) { child.kill('SIGKILL'); stopped = await exited; result.forced_shutdown = true; }
  result.host_exit = stopped;
  await log.close();
  result.ended_at = new Date().toISOString();
  // A finished turn alone is not proof of an actual model response or plugin
  // success. Native request/response and tool results are reviewed separately.
  await save('result.json', result);
}
console.log(JSON.stringify(result, null, 2));
if (result.errors.length || !result.turn_completed) process.exitCode = 2;
