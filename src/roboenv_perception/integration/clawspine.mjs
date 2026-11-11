#!/usr/bin/env node
/** Reuse native ClawSpine; EnvCreator never owns a second robot agent loop. */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { inspectRuntime, resolvedModel } from './runtime.mjs';
import { loadModelConfig } from './model_config.mjs';

export async function prepareClawSpine(argv, { python = process.env.ENVCREATOR_PYTHON ?? 'python3' } = {}) {
  const own = {}, nativeArgs = [];
  for (let i = 0; i < argv.length; i++) {
    const option = argv[i];
    if (option === '--dry-run' || option === '--doctor') {
      if (own.dryRun || own.doctor) throw new Error('Choose --dry-run or --doctor once');
      own[option === '--doctor' ? 'doctor' : 'dryRun'] = true;
    } else if (option === '--dsh-model') {
      const value = argv[++i];
      if (own.selectedModel || !value || value.startsWith('--')) throw new Error('Missing or duplicate --dsh-model');
      own.selectedModel = value;
    } else if (['--bundle', '--clawspine-root', '--receipt', '--dsh-model-config'].includes(option)) {
      const value = argv[++i];
      if (own[option] || !value || value.startsWith('--')) throw new Error('Missing or duplicate ' + option);
      own[option] = resolve(value);
    } else {
      if (['--scene-usd', '--scene-config', '--scene-id'].includes(option)) throw new Error('Scene identity comes from the EnvCreator bundle');
      nativeArgs.push(option);
    }
  }
  if (!own['--bundle'] || !own['--clawspine-root']) throw new Error('--bundle and --clawspine-root are required');
  const inspected = spawnSync(python, ['-m', 'roboenv_perception.integration.cli', 'inspect', '--bundle', own['--bundle']], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
  if (inspected.error) throw inspected.error;
  if (inspected.status !== 0) throw new Error('EnvCreator bundle rejected: ' + inspected.stderr.trim());
  const bundle = JSON.parse(inspected.stdout);
  const root = own['--clawspine-root'], launcher = join(root, 'scripts/launch_dsh_rsi.mjs');
  if (!existsSync(launcher)) throw new Error('ClawSpine source needs scripts/launch_dsh_rsi.mjs');
  // Use the real native argument parser, lifecycle and controller ownership.
  const { rsiOptions, launchRsi } = await import(pathToFileURL(launcher).href);
  if (own.selectedModel && !own['--dsh-model-config']) throw new Error('--dsh-model requires --dsh-model-config');
  const modelConfig = own['--dsh-model-config'] ? loadModelConfig(own['--dsh-model-config'], python, own.selectedModel) : null;
  if (modelConfig && nativeArgs.some(arg => ['--model-provider', '--model', '--model-base-url', '--provider-dsh-home', '--dsh-credential-env'].includes(arg))) {
    throw new Error('Local DSH config owns model selection and credentials; remove conflicting native model options');
  }
  const cfg = rsiOptions(['--scene-usd', bundle.scene_usd, '--scene-id', bundle.scene_id, ...nativeArgs,
    ...(modelConfig ? ['--model-provider', modelConfig.summary.provider, '--model', modelConfig.summary.model,
      '--no-inherit-providers', '--dsh-credential-env', modelConfig.summary.credential_environment] : [])]);
  let temporary;
  if (modelConfig) {
    // The native launcher snapshots this non-secret patch into its run directory.
    const pluginPatch = cfg.nativeExtraPatch ? readFileSync(cfg.nativeExtraPatch, 'utf8') : '';
    temporary = mkdtempSync(join(tmpdir(), 'envcreator-dsh-'));
    cfg.nativeExtraPatch = join(temporary, 'gateway.patch.yml');
    writeFileSync(cfg.nativeExtraPatch, pluginPatch + '\n' + modelConfig.patch, { flag: 'wx', mode: 0o600 });
  }
  const revision = spawnSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' });
  const changed = spawnSync('git', ['-C', root, 'status', '--porcelain'], { encoding: 'utf8' });
  const sourceFiles = ['scripts/launch_dsh_rsi.mjs', 'scripts/launch_dsh_sim.mjs', 'scripts/rsi/model-selection.mjs'];
  const receipt = {
    schema: 'envcreator.clawspine-handoff.v1',
    bundle: bundle.bundle, manifest_sha256: bundle.manifest_sha256,
    delivery_status: bundle.delivery_status,
    clawspine_source: { root, commit: revision.status === 0 ? revision.stdout.trim() : null,
      working_tree_modified: changed.status === 0 ? Boolean(changed.stdout.trim()) : null,
      launcher_sha256: createHash('sha256').update(readFileSync(launcher)).digest('hex'),
      source_files_sha256: Object.fromEntries(sourceFiles.map(file => [file, createHash('sha256').update(readFileSync(join(root, file))).digest('hex')])) },
    scene: { usd: cfg.sceneUsd, id: cfg.sceneId, spawn: cfg.spawn, observer_eye: cfg.observerEye, observer_target: cfg.observerTarget },
    agent_mode: cfg.agentMode, control_mode: cfg.controlMode,
    model_selection: { ...await resolvedModel(root, cfg), source: modelConfig ? 'independent local DSH config through native pi-ai' : 'native ClawSpine configuration' },
    ...(modelConfig ? { gateway: modelConfig.summary } : {}),
    check: 'bundle_hashes_and_native_launcher_arguments',
    native_execution: 'not_run', robot_task: 'not_submitted',
  };
  return { cfg, receipt, launchRsi, dryRun: Boolean(own.dryRun), doctor: Boolean(own.doctor), receiptPath: own['--receipt'],
    modelEnvironment: modelConfig?.environment ?? {}, cleanup: () => { if (temporary) rmSync(temporary, { recursive: true, force: true }); } };
}

export async function main(argv) {
  if (argv.includes('--help')) {
    console.log(`Usage: envcreator clawspine --bundle DIRECTORY --clawspine-root CHECKOUT
  --spawn X,Y,FLOOR_Z,YAW --observer-eye X,Y,Z --observer-target X,Y,Z
  [--dsh-model-config PRIVATE_TOML] [--dsh-model MODEL_ID]
  [--dry-run | --doctor] [--receipt NEW_JSON_FILE] [native ClawSpine launcher options]

--dry-run verifies scene hashes and the real native launcher arguments without
starting a simulator, model or robot. The source checkout may be ClawSpine or
RoboHelix containing ClawSpine. No evolution campaign is used.
--doctor also checks actual build artifacts, pinned image, GPU and provider prerequisites.
Actual launch inherits native DSH observation, tools, UI, control and cancellation.
It needs that checkout's installed dependencies, Isaac runtime and controller weights.
Only scene/ is mounted into the body; source task/evaluator metadata stays outside.
No robot task is automatically submitted. Delivery status is preserved verbatim.`);
    return;
  }
  const plan = await prepareClawSpine(argv);
  const previousEnvironment = Object.fromEntries(Object.keys(plan.modelEnvironment).map(name => [name, process.env[name]]));
  try {
    const report = plan.doctor ? await inspectRuntime(plan) : plan.receipt;
    if (plan.receiptPath) writeFileSync(plan.receiptPath, JSON.stringify(report, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    console.log(JSON.stringify(report, null, 2));
    if (plan.doctor) { if (report.status === 'blocked') process.exitCode = 2; return; }
    if (!plan.dryRun) {
      // ClawSpine strips secret environment variables from builds and containers;
      // its explicit credential selector forwards only this key to the final DSH.
      Object.assign(process.env, plan.modelEnvironment);
      await plan.launchRsi(plan.cfg, { onReady: async ({ state, signal }) => {
        const ready = { ...plan.receipt, native_execution: 'session_ready', run_id: state.runId };
        writeFileSync(join(state.runDir, 'envcreator-handoff.json'), JSON.stringify(ready, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
        await new Promise(done => { signal.addEventListener('abort', done, { once: true }); if (signal.aborted) done(); });
      } });
    }
  } finally {
    for (const [name, value] of Object.entries(previousEnvironment)) {
      if (value === undefined) delete process.env[name]; else process.env[name] = value;
    }
    plan.cleanup();
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch(error => { console.error('EnvCreator: ' + error.message); process.exitCode = 1; });
}
