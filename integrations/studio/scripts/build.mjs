import { build } from "esbuild";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { Script } from "node:vm";
await mkdir("dist", { recursive: true });
const catalog = spawnSync("python3", ["-c", "import runpy,json;d=runpy.run_path('../../src/roboenv_perception/components/catalog.py');print(json.dumps(d['describe']()))"], {encoding: "utf8"});
if (catalog.status !== 0) throw Error("Cannot build component catalog: " + catalog.stderr);
JSON.parse(catalog.stdout);
await writeFile("dist/catalog.json", catalog.stdout);
try {
  await build({
    entryPoints: ["src/client.jsx"],
    outfile: "dist/client.next.js",
    bundle: true,
    platform: "browser",
    format: "cjs",
    target: "es2022",
    minify: true,
    external: ["react", "react/*", "@deepseek-ai/*"],
    banner: {
      js: 'window.__ModuleLoader__.load({id:"@envcreator/studio",factory:(require)=>{var module={exports:{}};var exports=module.exports;',
    },
    footer: { js: "return module.exports;}});" },
    loader: { ".css": "text" },
  });
  new Script(await readFile("dist/client.next.js", "utf8"));
  await rename("dist/client.next.js", "dist/client.js");
} finally {
  await rm("dist/client.next.js", { force: true });
}
