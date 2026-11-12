/** Boot a dedicated native DSH profile without any robot or simulator launcher. */
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync, mkdirSync, realpathSync, createWriteStream } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';

const binding = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const cliRoot = join(binding.dshRoot, 'node_modules/@deepseek-ai/dsh');
if (JSON.parse(readFileSync(join(cliRoot, 'package.json'))).version !== '0.2.1-alpha.1') throw new Error('Studio requires native DSH 0.2.1-alpha.1');
const cli = realpathSync(join(cliRoot, 'lib/bin.js'));
const profile = 'envcreator-studio';
const env = { ...process.env, DSH_HOME: join(binding.stateRoot, 'dsh-home') };
// Deliberately do not forward SceneCreator VLM credentials to the task model.
delete env.VLM_API_KEY;
const log = createWriteStream(join(binding.stateRoot, 'dsh-private.log'), { flags: 'a', mode: 0o600 });
let child, stopping = false;
const run = (args, label) => new Promise((done, reject) => {
  const processChild = spawn(process.execPath, [cli, ...args], { env, cwd: binding.workspace, stdio: ['ignore', 'pipe', 'pipe'] });
  child = processChild;
  processChild.stdout.pipe(log, { end: false }); processChild.stderr.pipe(log, { end: false });
  processChild.once('error', reject);
  processChild.once('close', code => code === 0 ? done() : reject(new Error(label + ' exited ' + code + '; inspect private DSH log')));
});
const stop = () => { stopping = true; child?.kill('SIGTERM'); };
process.on('SIGTERM', stop); process.on('SIGINT', stop);
if (!existsSync(join(env.DSH_HOME, 'profiles', profile, 'package.json'))) {
  await run(['--profile', profile, '--from-default-profile', 'web', '--dump-config'], 'profile bootstrap');
}
if (stopping) process.exit(0);
await run(['plugin', '--profile', profile, 'add', '--ignore-scripts', binding.pluginRoot], 'studio plugin install');
if (stopping) process.exit(0);
const nativePath = join(binding.stateRoot, 'native.json');
const previous = existsSync(nativePath) ? JSON.parse(readFileSync(nativePath)) : {};
const sessionId = previous.session_id ?? randomUUID();
writeFileSync(nativePath, JSON.stringify({ session_id: sessionId, workspace: binding.workspace }), { mode: 0o600 });
const config = { backendURL: binding.backendURL, tokenFile: binding.tokenFile,
  maxWallSeconds: binding.maxWallSeconds, debugComponentTools: binding.debugComponentTools, defaultSessionId: sessionId };
const rows = [
  { id: 'llm-deepseek', disabled: true },
  ...(!binding.modelConfigured ? [{ id: 'agent-default-model', config: { provider: 'envcreator-unconfigured', model: 'configure-in-settings' } }] : []),
  { insert: [
    { id: 'envcreator-studio', name: '@envcreator/studio', config },
    { id: 'preset-envcreator-studio', name: '@deepseek-ai/dsh-agent-preset', config: {
      id: 'envcreator-studio', plugins: [{ id: 'envcreator-studio-tools', name: join(binding.pluginRoot, 'scope.mjs'), config }] } },
  ] },
];
const patch = join(binding.stateRoot, 'studio.patch.yml');
writeFileSync(patch, rows.map(row => '- ' + JSON.stringify(row)).join('\n') + '\n', { mode: 0o600 });
const args = ['--profile', profile, '--patch', patch];
if (binding.modelConfigured) args.push('--patch', join(binding.stateRoot, 'model.patch.yml'));
let output = '';
child = spawn(process.execPath, [cli, ...args, '--port', String(binding.port), '--no-open'], { env, cwd: binding.workspace, stdio: ['ignore', 'pipe', 'pipe'] });
child.stdout.pipe(log, { end: false }); child.stderr.pipe(log, { end: false });
child.stdout.on('data', chunk => { output = (output + chunk.toString()).slice(-100000); });
child.stderr.on('data', chunk => { output = (output + chunk.toString()).slice(-100000); });
let exited = false;
child.once('exit', () => { exited = true; });
const { connectDshWeb, dshWebUrlFromOutput } = await import(pathToFileURL(join(binding.dshRoot, 'scripts/dsh/web-auth.mjs')));
const { nativeRpc } = await import(pathToFileURL(join(binding.dshRoot, 'scripts/rsi/native-session.mjs')));
let launchUrl;
const deadline = Date.now() + 120000;
while (!stopping && !exited && Date.now() < deadline) {
  launchUrl = dshWebUrlFromOutput(output);
  if (launchUrl) break;
  await new Promise(done => setTimeout(done, 200));
}
if (!launchUrl) { stop(); throw new Error('Native DSH did not become ready; inspect dsh-private.log'); }
try {
  const connection = await connectDshWeb(launchUrl); await connection.response.body?.cancel();
  const directory = await nativeRpc(connection, 'session/list', { args: { _request: {} } }, AbortSignal.timeout(15000));
  if (!directory.items?.some(item => item.sessionId === sessionId)) {
    await nativeRpc(connection, 'session/create', { args: { request: { cwd: binding.workspace, sessionId, agentPreset: 'envcreator-studio' } } }, AbortSignal.timeout(30000));
  }
  if (binding.modelConfigured) {
    await nativeRpc(connection, 'session/selectModel', { args: { request: { sessionId, provider: binding.model.provider, model: binding.model.model } } }, AbortSignal.timeout(15000));
  }
  writeFileSync(join(binding.stateRoot, 'ready.json'), JSON.stringify({ web_url: launchUrl, session_id: sessionId, ready_at: new Date().toISOString(), robot_execution: 'not_run' }), { mode: 0o600 });
  console.log('SceneCreator Studio ready: ' + launchUrl);
  console.log('Native DSH session: ' + sessionId + ' | robot/simulator launch: none');
  if (binding.open) spawn('xdg-open', [launchUrl], { stdio: 'ignore' }).on('error', () => {});
} catch (error) { stop(); throw error; }
await new Promise(done => { if (exited) done(); else child.once('exit', done); });
log.end();
if (!stopping) process.exitCode = 1;
