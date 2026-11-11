import { registerTools } from "./host.mjs";
export const name = "envcreator-studio-scope";
export const inject = ["tools", "attachments"];
export function apply(ctx, config) {
  registerTools(ctx, config);
  ctx.tools.presentAs("native");
}
