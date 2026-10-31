#!/usr/bin/env node
/** Submit one public goal to native DSH; read evidence without operating the robot. */
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

export function measuredQuiescence(sample) {
  const execution = sample?.execution, measured = sample?.measurements;
  if (execution?.resourcesReleased !== true) return false;
  if (execution.controllerQuiescent === true) return true;
  // An idle controller has no execution receipt. Require actual velocity
  // measurements instead of treating the absence of an action as a stop.
  return Boolean(execution.state === 'idle' && measured &&
    [['linearSpeedMps', .10], ['angularSpeedRadps', .20], ['upperJointSpeedRadps', .20]]
      .every(([key, limit]) => Number.isFinite(measured[key]) && measured[key] >= 0 && measured[key] <= limit));
}

export async function recordSmoke({ root, runDir, output, task, wallSeconds = 240 }) {
  const state = JSON.parse(readFileSync(join(runDir, 'run.json'), 'utf8'));
  if (state.agentMode !== 'native' || !state.sessionId || state.stage !== 'interactive') throw new Error('A ready native ClawSpine session is required');
  mkdirSync(output, { mode: 0o700 });
  const save = (name, data) => writeFileSync(join(output, name), JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
  const result = { schema: 'envcreator.native-smoke.v1', scope: 'unscored-observation-and-basic-action',
    task, session_id: state.sessionId, model: state.modelSelection, scene: state.scene,
    request_id: randomUUID(), dispatched: false, turn_completed: false, physical_quiescence: false,
    semantic_task_success: 'not_judged', errors: [] };
  save('definition.json', { ...result, wall_seconds: wallSeconds, outer_robot_action_calls: false });
  let snapshot, client, connection;
  try {
    const { connectArenaDsh } = await import(pathToFileURL(join(root, 'scripts/arena/dsh-client.mjs')));
    const { connectDshWeb } = await import(pathToFileURL(join(root, 'scripts/dsh/web-auth.mjs')));
    client = await connectArenaDsh(state.webUrl);
    connection = await connectDshWeb(state.webUrl); await connection.response.body?.cancel();
    const admin = readFileSync(state.adminTokenFile, 'utf8').trim();
    const actor = readFileSync(state.actorTokenFile, 'utf8').trim();
    const read = async (path, token) => {
      const response = await fetch('http://127.0.0.1:' + state.backendPort + path, {
        headers: { authorization: 'Bearer ' + token }, signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error('Simulation evidence HTTP ' + response.status);
      return response;
    };
    connection.audit = async () => (await read('/v1/audit-snapshot', admin)).json();
    connection.frames = async (phase) => {
      const frames = [];
      for (const kind of ['rgb', 'third_person']) {
        const response = await read('/v1/frame?kind=' + kind + '&eye=left&resolution=half', actor);
        if (!response.headers.get('content-type')?.startsWith('image/png')) throw new Error('Simulation frame is not PNG');
        const filename = phase + '-' + kind + '.png';
        writeFileSync(join(output, filename), Buffer.from(await response.arrayBuffer()), { mode: 0o600 });
        frames.push({ file: filename, headers: Object.fromEntries([...response.headers].filter(([key]) => key.startsWith('x-frame-'))) });
      }
      save(phase + '-frames.json', frames);
    };
    const initial = await client.snapshot(state.sessionId, { maxMessages: 1000 });
    if (initial.records.some(row => row.event?.type === 'user/message' && row.event.data?.source?.kind === 'user')) throw new Error('Use a fresh native session for this smoke');
    save('before-audit.json', await connection.audit());
    await connection.frames('before');
    result.dispatched = true; result.started_at = new Date().toISOString();
    save('dispatch-attempt.json', result);
    await client.prompt(state.sessionId, task, { requestId: result.request_id });
    const deadline = Date.now() + wallSeconds * 1000;
    while (Date.now() < deadline) {
      snapshot = await client.snapshot(state.sessionId, { maxMessages: 5000 });
      const events = snapshot.records.map(row => row.event).filter(Boolean);
      const user = events.find(e => e.type === 'user/message' && e.data?.source?.rpcId === result.request_id);
      const ended = user && events.find(e => e.type === 'turn/end' && e.seq > user.seq);
      if (ended) { result.turn_completed = true; result.native_turn_reason = ended.data?.reason; break; }
      await delay(1000);
    }
    result.end_reason = result.turn_completed ? 'native_turn_ended' : 'wall_timeout';
    save('at-boundary-audit.json', await connection.audit());
    await connection.frames('at-boundary');
  } catch (error) { result.errors.push(String(error.message).replace(/https?:\/\/[^\s"']+/g, '[private-url]')); }
  finally {
    if (result.dispatched) {
      try { await client.cancel(state.sessionId); } catch (e) { result.errors.push('cancel: ' + e.message); }
      // Independent human stop, never a robot action or scene-state write.
      try {
        const rpcId = randomUUID();
        const response = await connection.fetch('/clawspine-native/stop', { method: 'POST',
          headers: { 'content-type': 'application/json', origin: connection.origin },
          body: JSON.stringify({ type: 'client-request', rpcId, method: 'stop', payload: {} }), signal: AbortSignal.timeout(45000) });
        const stopped = await response.json();
        if (!response.ok || stopped.rpcId !== rpcId || !stopped.result?.ok) throw new Error('Native human-stop failed');
        save('human-stop.json', stopped.result.value);
        const final = await connection.audit(); save('after-stop-audit.json', final);
        result.physical_quiescence = measuredQuiescence(final);
        await connection.frames('after-stop');
      } catch (e) { result.errors.push('stop: ' + e.message); }
      try { snapshot = await client.snapshot(state.sessionId, { maxMessages: 5000 }); } catch (e) { result.errors.push('snapshot: ' + e.message); }
    }
    if (snapshot) save('native-trajectory.json', snapshot);
    result.ended_at = new Date().toISOString(); save('result.json', result); client?.close();
  }
  return result;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const parser = await import('node:util');
  const { values } = parser.parseArgs({ options: { 'clawspine-root': { type: 'string' },
    'run-dir': { type: 'string' }, output: { type: 'string' }, task: { type: 'string' }, 'wall-seconds': { type: 'string', default: '240' } } });
  if (!values['clawspine-root'] || !values['run-dir'] || !values.output || !values.task) throw new Error('--clawspine-root, --run-dir, --output and --task are required');
  const wallSeconds = Number(values['wall-seconds']);
  if (!Number.isFinite(wallSeconds) || wallSeconds <= 0) throw new Error('Positive --wall-seconds required');
  recordSmoke({ root: resolve(values['clawspine-root']), runDir: resolve(values['run-dir']),
    output: resolve(values.output), task: values.task, wallSeconds }).then(result => {
    console.log(JSON.stringify(result, null, 2));
    if (result.errors.length || !result.turn_completed || !result.physical_quiescence) process.exitCode = 2;
  }).catch(error => { console.error(error.message); process.exitCode = 1; });
}
