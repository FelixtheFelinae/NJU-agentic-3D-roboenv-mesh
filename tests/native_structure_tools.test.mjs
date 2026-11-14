// Native plugin protocol tests with a fake loopback backend; no model/network calls.
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { registerTools } from '../integrations/studio/host.mjs';

test('native tool defaults to native structure author and exposes full submission', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'scenecreator-structure-'));
  const originalFetch = globalThis.fetch;
  try {
    const tokenFile = join(directory, 'token');
    writeFileSync(tokenFile, 'synthetic-test-token');
    const tools = new Map();
    const requests = [];
    globalThis.fetch = async (_url, options) => {
      requests.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ id: 'run-1', status: 'completed' }), {
        headers: { 'content-type': 'application/json' },
      });
    };
    registerTools({ tools: { register: definition => tools.set(definition.name, definition) } }, {
      backendURL: 'http://127.0.0.1:12345/', tokenFile, maxWallSeconds: 60,
    });
    const start = tools.get('envcreator_workflow_start');
    await start.execute({ task: 'Generate a plausible drawer', input_ids: ['image-1'] }, {
      agent: { id: 'session-1' }, callId: 'call-1',
    });
    assert.equal(requests[0].params.structure_author, 'native_dsh');
    assert.equal(requests[0].session_id, 'session-1');
    assert.equal(requests[0].call_id, 'call-1');
    await start.execute({ task: 'Explicit legacy experiment', input_ids: ['image-1'],
      params: { structure_author: 'internal_vlm' } }, {
      agent: { id: 'session-1' }, callId: 'call-2',
    });
    assert.equal(requests[1].params.structure_author, 'internal_vlm');
    const advance = tools.get('envcreator_workflow_advance');
    assert.ok(advance.parameters.properties.params.properties.action.enum.includes('submit_structure'));
    assert.equal(advance.parameters.properties.params.properties.blueprint.type, 'object');
  } finally {
    globalThis.fetch = originalFetch;
    rmSync(directory, { recursive: true, force: true });
  }
});
