/** Check the real ClawSpine deployment without allocating GPU or creating a session. */
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export async function resolvedModel(root, cfg) {
  const { nativeModelSelection } = await import(pathToFileURL(join(root, 'scripts/rsi/model-selection.mjs')).href);
  return nativeModelSelection(cfg);
}

export async function inspectRuntime(plan, { probe = spawnSync, environment = process.env } = {}) {
  const root = plan.receipt.clawspine_source.root, checks = [];
  const add = (name, status, details) => checks.push({ name, status, ...details });
  add('node', Number(process.versions.node.split('.')[0]) === 24 ? 'passed' : 'blocked', { version: process.versions.node });
  const packages = ['packages/core', 'packages/dsh-runtime', 'packages/dsh-ui',
    'playground/packages/platform-g1', 'playground/packages/platform-g1-dsh', 'playground/packages/platform-g1-arena-dsh'];
  for (const relative of packages) {
    const path = join(root, relative, 'package.json');
    if (!existsSync(path)) { add(relative, 'blocked', { reason: 'source_package_missing' }); continue; }
    const manifest = JSON.parse(readFileSync(path, 'utf8'));
    const entries = [manifest.main ?? manifest.exports?.['.'], manifest.exports?.['./native']].filter(Boolean)
      .map(value => typeof value === 'string' ? value : value.import ?? value.default);
    const missing = entries.filter(value => !value || !existsSync(join(root, relative, value)));
    add(relative, missing.length || !entries.length ? 'blocked' : 'passed', { package: manifest.name, missing });
  }
  const { installedDshCommand } = await import(pathToFileURL(join(root, 'scripts/launch_dsh_sim.mjs')).href);
  try { add('native_dsh', 'passed', { cli: installedDshCommand([]).args[0] }); }
  catch (error) { add('native_dsh', 'blocked', { reason: error.message }); }
  const { images } = await import(pathToFileURL(join(root, 'scripts/arena/launch-support.mjs')).href);
  const arenaImage = plan.cfg.arenaImageId ?? images.arena;
  if (plan.cfg.runtimeBackend === 'docker') {
    const sudo = environment.CLAWSPINE_DOCKER_SUDO === '1';
    const inspected = probe(sudo ? 'sudo' : 'docker', [...(sudo ? ['-n', 'docker'] : []),
      'image', 'inspect', '--format', '{{.Id}}', arenaImage], { encoding: 'utf8', timeout: 15000 });
    const matched = inspected.status === 0 && inspected.stdout.trim() === arenaImage;
    add('arena_image', matched ? 'passed' : 'blocked',
      { required: arenaImage, reason: matched ? 'identity_checked' : 'pinned_image_unavailable' });
  } else {
    add('arena_runtime', 'unverified', { reason: 'process_backend_requires_an_independently_verified_container_deployment' });
  }
  const gpu = probe('nvidia-smi', ['-i', plan.cfg.gpu ?? '0', '--query-gpu=index,uuid,name,memory.free', '--format=csv,noheader'],
    { encoding: 'utf8', timeout: 10000 });
  add('gpu', gpu.status === 0 ? 'passed' : 'blocked', { observation: gpu.status === 0 ? gpu.stdout.trim() : 'GPU_query_failed' });
  add('isaac_license', environment.ACCEPT_EULA === 'Y' ? 'passed' : 'blocked',
    { requirement: 'Existing ClawSpine launcher requires ACCEPT_EULA=Y after user acceptance of Isaac terms' });
  const model = await resolvedModel(root, plan.cfg);
  if (model.provider === 'codex') {
    const { inspectSubscriptionAuthHome, DEFAULT_SUBSCRIPTION_DSH_HOME } = await import(pathToFileURL(join(root, 'scripts/ai/subscription-provider.mjs')).href);
    const auth = inspectSubscriptionAuthHome({ dshHome: plan.cfg.subscriptionDshHome ?? DEFAULT_SUBSCRIPTION_DSH_HOME });
    add('model_credentials', auth.credentialFileAvailable ? 'unverified' : 'blocked',
      { provider: model.provider, credential_file_available: auth.credentialFileAvailable,
        authenticated: 'not_checked', reason: 'OAuth is performed only by the user in the native provider UI' });
  } else add('model_credentials', 'unverified', { provider: model.provider, authenticated: 'not_checked' });
  return { ...plan.receipt, check: 'local_runtime_prerequisites', checks,
    status: checks.some(row => row.status === 'blocked') ? 'blocked' : 'prerequisites_present_live_execution_unverified',
    native_execution: 'not_run', physics_execution: 'not_run' };
}
