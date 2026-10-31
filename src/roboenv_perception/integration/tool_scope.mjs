/** Optional asset-only native DSH preset. Robot sessions retain native robot tools. */
import { apply as registerTools } from './tool_plugin.mjs';
export const name = 'envcreator-asset-scope';
export const inject = ['tools'];
export async function apply(ctx, config) {
  // Native presets have an isolated tool layer. Register only these scoped
  // tools rather than trying to filter a global layer that the preset omits.
  await registerTools(ctx, config);
  ctx.tools.presentAs('native');
}
