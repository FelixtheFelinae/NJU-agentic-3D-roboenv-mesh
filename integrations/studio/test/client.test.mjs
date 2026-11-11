import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import React from "react";
import Renderer, { act } from "react-test-renderer";

function bundle(fetch) {
  let plugin;
  const context = {
    window: {
      __ModuleLoader__: {
        load: ({ factory }) => {
          plugin = factory((id) => {
            if (id === "react") return React;
            throw Error(id);
          });
        },
      },
    },
    fetch,
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    console,
    URL,
    URLSearchParams,
    crypto: {randomUUID: () => "user-request"},
    FormData,
    performance,
    TextDecoder,
    TextEncoder,
    Blob,
    ArrayBuffer,
    Uint8Array,
  };
  vm.runInNewContext(
    readFileSync(new URL("../dist/client.js", import.meta.url), "utf8"),
    context,
  );
  return plugin;
}

test("native workbench loads actual run projection, changes views and clears old session state", async (t) => {
  const calls = [],
    components = {},
    disposers = [],
    prompts = [];
  const response = (value) => ({ ok: true, json: async () => value });
  const fetch = async (path, init = {}) => {
    calls.push([path, init.body]);
    if (path.endsWith("/artifacts/scope-report"))
      return { ok: true, text: async () => '{"all_resolved":false}' };
    if (path.endsWith("/bind")) {
      const body = JSON.parse(init.body);
      return response({
        id: body.session_id,
        title: body.session_id === "session-a" ? "瓶子还原" : "新任务",
        runs:
          body.session_id === "session-a"
            ? [{ id: "run-a", status: "completed" }]
            : [],
        inputs: [],
      });
    }
    if (path.endsWith("/selection")) return response({selected:true});
    if (path.includes("/tasks/session-b/workspace?")) return response({sources:{},objects:[],decisions:[],stale:[]});
    if (path.includes("/workspace?")) return response({sources:{},objects:[],decisions:[{kind:"scope",title:"重建范围需要确认",message:"对象可能重复，请选择重建主体。"}],stale:[],
      perception:{frames:[{id:"frame-a",image:{run_id:"run-a",id:"image-a"},objects:[]}]}});
    if (path.endsWith("/tasks")) return response([]);
    if (path.endsWith("/config"))
      return response({
        settings: { state_root: "/fixture" },
        model: { model: "fixture-model" },
      });
    if (path.endsWith("/telemetry"))
      return response({
        latest: {
          at: Date.now() / 1000,
          host: "fixture-host",
          cpu_percent: 12,
          memory_used: 1024,
          gpus: [],
          processes: [],
        },
        history: [],
      });
    if (path.endsWith("/tasks/session-a"))
      return response({
        id: "session-a",
        title: "瓶子还原",
        runs: [{ id: "run-a", status: "completed" }],
        inputs: [],
      });
    if (path.endsWith("/runs/run-a"))
      return response({
        id: "run-a",
        status: "completed",
        outcome: {
          validation_status: "unverified",
          physics_status: "not_run",
          blocked_assets: 3,
          blockers: [
            {
              reason: "部件划分尚未通过独立审核",
              report_ref: "output/scope/scope_plan.json",
            },
          ],
        },
        artifacts: {
          items: [
            {
              id: "image-a",
              ref: "render.png",
              name: "render.png",
              kind: "image",
            },
            {
              id: "scope-report",
              ref: "output/scope/scope_plan.json",
              name: "scope_plan.json",
              kind: "evidence",
            },
          ],
        },
        events: [],
      });
    throw Error(path);
  };
  const plugin = bundle(fetch);
  const ctx = {
    connection: {rpc:{call: async (path, method, body) => {prompts.push({path,method,body});return {ok:true,value:{accepted:true}};}}},
    effect: (fn) => disposers.push(fn()),
    slots: {
      register: (spec, component) => {
        components[spec.name] = component;
        return () => {};
      },
      inject: (_name, fn) => fn(),
    },
    sidebarRightTabs: { register: () => () => {} },
    sidebarRight: { openTab: () => {} },
    sessions: {
      list: {
        getSnapshot: () => ({ phase: "loading", byId: {} }),
        subscribe: () => () => {},
      },
    },
  };
  plugin.apply(ctx);
  const Workbench = components["sidebar.right.pane.tab"];
  assert.equal(typeof Workbench, "function");
  let renderer;
  t.after(async () => {
    if (renderer) await act(async () => renderer.unmount());
    for (const dispose of disposers.reverse()) dispose?.();
  });
  await act(async () => {
    renderer = Renderer.create(
      React.createElement(Workbench, { sessionId: "session-a" }),
    );
  });
  assert.ok(JSON.stringify(renderer.toJSON()).includes("瓶子还原"));
  const textContent = (value) =>
    value == null
      ? ""
      : typeof value === "object"
        ? (Array.isArray(value) ? value : (value.children ?? []))
            .map(textContent)
            .join("")
        : String(value);
  await act(async () => renderer.root.findByProps({"aria-label":"描述修改要求"}).props.onChange({target:{value:"保留当前结构，只调整屏幕比例。"}}));
  await act(async () => renderer.root.findByType("form").props.onSubmit({preventDefault(){}}));
  assert.equal(prompts.length,1);
  assert.equal(prompts[0].path,"/api");
  assert.equal(prompts[0].method,"session/prompt");
  assert.equal(prompts[0].body.args.request.sessionId,"session-a");
  assert.equal(prompts[0].body.args.request.mode,"queue");
  assert.equal(prompts[0].body.args.request.content[0].text,"保留当前结构，只调整屏幕比例。");
  assert.equal(renderer.root.findByProps({"aria-label":"描述修改要求"}).props.value,"");
  const rendered = textContent(renderer.toJSON());
  assert.ok(rendered.includes("未验证"));
  assert.ok(rendered.includes("重建范围需要确认"));
  assert.ok(rendered.includes("输入与分割"));
  assert.ok(!rendered.includes("执行时间线"));
  await act(async () => renderer.root.findAllByType("button").find(v => v.props.children === "技术详情").props.onClick());
  const technical = textContent(renderer.toJSON());
  assert.ok(technical.includes("部件划分尚未通过独立审核"));
  assert.ok(technical.includes("物理验证：未执行"));
  assert.ok(technical.includes("3 项资产被阻塞"));
  assert.equal(
    renderer.root
      .findAllByType("img")
      .some((v) => v.props.src.endsWith("/run-a/artifacts/image-a")),
    true,
  );
  await act(async () =>
    renderer.root
      .findAllByType("button")
      .find((v) => v.props.children === "查看原始审核")
      .props.onClick(),
  );
  assert.equal(
    renderer.root.findByProps({ "aria-label": "选择产物" }).props.value,
    "scope-report",
  );
  const monitor = renderer.root
    .findAllByType("button")
    .find((v) => v.props.children?.includes?.("运行监控"));
  await act(async () => monitor.props.onClick());
  assert.ok(JSON.stringify(renderer.toJSON()).includes("执行节点与资源"));
  await act(async () =>
    renderer.update(React.createElement(Workbench, { sessionId: "session-b" })),
  );
  await act(async () =>
    renderer.root
      .findAllByType("button")
      .find((v) => v.props.children?.includes?.("生成工作台"))
      .props.onClick(),
  );
  assert.equal(renderer.root.findAllByType("img").length, 0);
  assert.ok(
    calls.some(
      ([path, body]) =>
        path.endsWith("/bind") && JSON.parse(body).session_id === "session-b",
    ),
  );
});
