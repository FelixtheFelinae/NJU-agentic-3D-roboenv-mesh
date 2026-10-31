import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { apply } from '../src/roboenv_perception/integration/tool_plugin.mjs';

test('plugin uses the real Python backend and correlates before/after with the native call', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'envcreator-plugin-'));
  try {
    const configFile = join(directory, 'config.json');
    const asset = join(directory, 'tool.usda');
    await writeFile(asset, '#usda 1.0\n');
    await writeFile(configFile, JSON.stringify({ asset, python: resolve('.venv/bin/python'),
      output_root: join(directory, 'calls') }));
    const definitions = new Map();
    await apply({ tools: { register: definition => definitions.set(definition.name, definition) } }, { configFile });
    const exec = { callId: 'native-call-fixture', agent: { id: 'native-agent-fixture' }, signal: new AbortController().signal };
    const result = await definitions.get('envcreator_inspect').execute({}, exec);
    assert.equal(result.robot_execution, 'not_run');
    assert.equal(result.asset, asset);
    const before = JSON.parse(await readFile(join(result.evidence_directory, 'before.json')));
    const after = JSON.parse(await readFile(join(result.evidence_directory, 'after.json')));
    assert.equal(before.asset_sha256, after.asset_sha256);
    assert.equal(after.bound_asset_unchanged, true);
    const events = (await readFile(join(directory, 'calls/plugin-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.deepEqual(events.map(row => row.event), ['loaded', 'before', 'after']);
    assert.equal(events[1].call_id, exec.callId);
    assert.equal(events[2].agent_id, exec.agent.id);
    await assert.rejects(definitions.get('envcreator_validate').execute({ command: 'not-a-tool-argument' }, exec), /takes no arguments/);
    await assert.rejects(definitions.get('envcreator_validate').execute({}, exec), /exited/);
    const failed = JSON.parse((await readFile(join(directory, 'calls/plugin-events.jsonl'), 'utf8')).trim().split('\n').at(-1));
    assert.equal(failed.event, 'failed');
    assert.equal(JSON.parse(await readFile(join(failed.evidence_directory, 'after.json'))).bound_asset_unchanged, true);
    assert.match(await readFile(join(failed.evidence_directory, 'failure.json'), 'utf8'), /not configured/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
