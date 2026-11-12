/** Native DSH domain plugin. No model client, chat loop or robot dependency. */
import { readFileSync } from "node:fs";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

export const name = "envcreator-studio";
export const inject = ["connection", "webServer", "tools", "systemPrompt", "attachments"];
const API = "/envcreator/api";
const components = JSON.parse(readFileSync(new URL("./dist/catalog.json", import.meta.url), "utf8"));

function backend(config) {
  const origin = new URL(config.backendURL);
  if (
    origin.protocol !== "http:" ||
    origin.hostname !== "127.0.0.1" ||
    origin.pathname !== "/"
  )
    throw new Error("Studio backend must be loopback");
  const token = readFileSync(config.tokenFile, "utf8").trim();
  return async (path, body, signal, image = false) => {
    const response = await fetch(new URL(path, origin), {
      method: body === undefined ? "GET" : "POST",
      headers: {
        authorization: "Bearer " + token,
        "content-type": "application/json",
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: signal ?? AbortSignal.timeout(30000),
    });
    if (image && response.ok) {
      if (!response.headers.get("content-type")?.startsWith("image/jpeg") ||
          Number(response.headers.get("content-length")) > 512 * 1024)
        throw new Error("Unexpected SceneCreator preview");
      const bytes = Buffer.from(await response.arrayBuffer());
      if (bytes.length > 512 * 1024) throw new Error("SceneCreator preview exceeds budget");
      return bytes;
    }
    const value = await response.json();
    if (!response.ok)
      throw new Error(value.detail ?? `SceneCreator HTTP ${response.status}`);
    return value;
  };
}

export function registerTools(ctx, config) {
  const request = backend(config);
  const sampling = { type: "object", additionalProperties: false, required: ["sample_fps", "max_frames"], properties: {
    start_seconds: { type: "number", minimum: 0 }, end_seconds: { type: "number", exclusiveMinimum: 0 },
    sample_fps: { type: "number", exclusiveMinimum: 0, maximum: 30 },
    max_frames: { type: "integer", minimum: 1, maximum: 64 }, reference_frame: { type: "integer", minimum: 0 },
  }};
  const positive = { type: "number", exclusiveMinimum: 0 };
  const nonnegative = { type: "number", minimum: 0 };
  const declaredDesign = { anyOf: [
    { type: "object", additionalProperties: false,
      required: ["kind", "screw_spec", "shaft_length_m", "thread"],
      properties: {
        kind: { const: "screw_cover" }, screw_spec: { enum: ["M3x0.5@1", "M4x0.7@1", "M5x0.8@1"] },
        shaft_length_m: positive, cover_thickness_m: positive, base_thickness_m: positive,
        outer_radius_m: positive, hole_diameter_m: positive,
        thread: { type: "object", additionalProperties: false,
          required: ["engagement_m", "unload_travel_m", "preload_N", "breakaway_torque_Nm", "running_torque_Nm"],
          properties: { engagement_m: positive, unload_travel_m: positive, preload_N: nonnegative,
            breakaway_torque_Nm: positive, running_torque_Nm: nonnegative } },
      } },
    { type: "object", additionalProperties: false, required: ["kind", "open_angle_deg"],
      properties: { kind: { const: "hinged_cover" }, open_angle_deg: { ...positive, maximum: 180 },
        size_x_m: positive, size_y_m: positive, cover_thickness_m: positive, base_thickness_m: positive } },
  ] };
  const definitions = [
    [
      "context",
      "Read the current session-bound task, uploaded input IDs, registered workflow and missing runtimes. Call before generation.",
      {},
    ],
    [
      "inspect",
      "Inspect a recorded run and its actual artifacts, stage errors and validation evidence. No new generation or simulation.",
      { run_id: { type: "string" } },
    ],
    ["workflow_start",
      "Start the fixed workflow with uploaded media or an explicit params.declared_design. For a declared screw cover or hinge, omit input_ids and leave physics and background off. It produces an unverified Isaac USD bundle. A video first asks for a sampling decision.",
      { task: { type: "string", minLength: 3, maxLength: 4000 },
        input_ids: { type: "array", items: { type: "string" }, minItems: 1, maxItems: 32 },
        params: { type: "object", additionalProperties: false, properties: {
          background: { enum: ["off", "masks", "reconstruct"] }, physics: { type: "boolean" },
          declared_design: declaredDesign,
        } },
      }],
    ["workflow_advance",
      "Advance the original workflow ID (parent_id). Read allowed_actions and revision. sample_video takes sampling; select_target takes the current object_id and reason justifying identity, plus optional evidence_refs tickets from retained_evidence and current objects[].views to choose at most 16 distinct real frames when fusing rounds. Include useful old and new observations. revisit_video takes sampling and reason. propose_build takes reason and hypothesis bound to blueprint_identity.sha256: an array of explicitly assumed parameters, never recovered measurements. Supported paths: /target_dimensions_m/x|y|z, /part_definitions/<id>/dimensions_m/x|y|z, /part_definitions/<id>/mass_kg, /joints/<name>/travel|rest. Units are m/kg/rad/convention. Do not overwrite known quantities, structural graph or relative units. repair_constraints takes reason and 1–4 existing constraint_ids from blueprint.geometric_constraints; it only rebinds those requirements and may add explicit plane/axis/edge interfaces, preserving all other fields. Use revise_structure for topology or intake changes. accept_structure/revise_structure/stop take reason. Candidate renders diagnose faults; they never enter real observation packets. resume takes only action.",
      { parent_id: { type: "string" }, params: {
        type: "object", additionalProperties: false, required: ["action"], properties: {
          action: { enum: ["sample_video", "select_target", "accept_structure", "revise_structure", "repair_constraints", "revisit_video", "propose_build", "stop", "resume"] },
          revision: { type: "integer", minimum: 0 }, sampling, object_id: { type: "string" },
          reason: { type: "string", minLength: 1, maxLength: 1500 },
          constraint_ids: { type: "array", minItems: 1, maxItems: 4, uniqueItems: true, items: { type: "string" } },
          evidence_refs: { type: "array", minItems: 1, maxItems: 16, uniqueItems: true, items: { type: "string" } },
          hypothesis: { type: "object", additionalProperties: false,
            required: ["source_blueprint_sha256", "parameters"], properties: {
              source_blueprint_sha256: { type: "string", pattern: "^[a-f0-9]{64}$" },
              parameters: { type: "array", minItems: 1, maxItems: 32, items: {
                type: "object", additionalProperties: false,
                required: ["path", "value", "unit", "rationale", "evidence_refs"],
                properties: {
                  path: { type: "string", maxLength: 200 },
                  value: { anyOf: [{ type: "number", exclusiveMinimum: 0 }, { enum: ["at_min", "at_max", "centered"] }] },
                  unit: { enum: ["m", "kg", "rad", "convention"] },
                  rationale: { type: "string", minLength: 3, maxLength: 1000 },
                  evidence_refs: { type: "array", minItems: 1, maxItems: 16, items: { type: "string" } },
                },
              }},
            }},
        },
      }}],
    ...(config.debugComponentTools ? [[
      "generate",
      "Reconstruct uploaded images or one video through the existing full SceneCreator Harness. Task must preserve user requirements. Each call creates a separate run; parent_id records lineage but does not edit or reuse an old mesh. Returns only after execution ends. Robot control is unavailable.",
      {
        task: { type: "string", minLength: 3, maxLength: 4000 },
        input_ids: {
          type: "array",
          items: { type: "string" },
          minItems: 1,
          maxItems: 32,
        },
        parent_id: { type: "string" },
      },
    ]] : []),
  ];
  for (const component of config.debugComponentTools ? components.filter(c => !c.id.startsWith("workflow_")) : []) definitions.push([
    component.id,
    `${component.title}. SceneCreator internal component. Input sources map roles to completed run IDs: ${JSON.stringify(component.inputs)}. Outputs: ${component.outputs.join(", ")}. Returns after execution, preserves versions. Use context/inspect for IDs. For single images use ingest -> inventory -> segment -> label; perceive handles video/multi-image. blueprint/geometry edits accept a previous source and full corrected task. Physics and scene export do not operate a robot.`,
    { task: {type: "string", maxLength: 4000},
      input_ids: {type: "array", items: {type: "string"}, maxItems: 32},
      sources: {type: "object", additionalProperties: {type: "string"}},
      object_id: {type: "string"},
      params: {type: "object", additionalProperties: true} },
  ]);
  for (const definition of definitions) if (definition[0] === "blueprint_repair") definition[1] += " params.constraint_ids selects 1–4 existing geometric requirements. One narrow internal VLM proposal changes only bindings/descriptions and may add plane/axis/edge interfaces. All other fields are preserved by the program. A rejected proposal retains the source and returns needs_revision with constraint_repair evidence. Independent blueprint_review remains required before building.";
  for (const definition of definitions) if (["blueprint", "geometry", "geometry_audit", "build"].includes(definition[0])) definition[1] += " AssemblyBlueprint workflow: geometry_audit checks actual recipe features (shaft/hole, finite overlap, clearance and explicit plane relations); it does not test exported meshes or physical behavior. Read status and feedback even when the tool finishes. Missing bindings or part-only constraints are unresolved. For repair, pass sources.geometric_verification plus the exact sources.previous to blueprint/geometry; feedback is checked against the old candidate hashes. repair_target=geometry permits local recipe repair; blueprint or verifier requests require semantic revision or an unsupported-capability response. Build recomputes checks. Unknown limits/rest are allowed in descriptions; later export requires explicit assumptions. Never silently substitute recovered contact surfaces for a candidate hinge_region.";
  const longOperation = (op) => op !== "context" && op !== "inspect";
  for (const [operation, description, properties] of definitions)
    ctx.tools.register({
      name: "envcreator_" + operation,
      description,
      parameters: {
        type: "object",
        properties,
        additionalProperties: false,
        ...(operation === "generate"
          ? { required: ["task", "input_ids"] }
          : operation === "workflow_start" ? { required: ["task"] }
          : operation === "workflow_advance" ? { required: ["parent_id", "params"] } : {}),
      },
      timeoutMs:
        longOperation(operation) ? (config.maxWallSeconds + 90) * 1000 : 30000,
      isConcurrencySafe: () => !longOperation(operation),
      output: {
        schema: { type: "object", additionalProperties: true },
        render: (_args, { preview_attachments, ...value }) => [
          { type: "text", text: JSON.stringify(value) },
          ...(preview_attachments ?? []).map(({ name, attachment }) => [
            { type: "text", text: name }, { type: "image", attachment },
          ]).flat(),
        ],
      },
      execute: async (args, exec) => {
        exec.signal?.throwIfAborted();
        if (!exec.agent?.id || !exec.callId)
          throw new Error("Native DSH execution identity required");
        const identity = {
          session_id: String(exec.agent.id),
          call_id: String(exec.callId),
        };
        const abort = () => {
          void request("/api/native/cancel", identity).catch(() => {});
        };
        if (longOperation(operation))
          exec.signal?.addEventListener("abort", abort, { once: true });
        try {
          const value = await request(
            "/api/native/invoke",
            { ...args, ...identity, operation },
            exec.signal,
          );
          if (!longOperation(operation)) return value;
          let run = value;
          while (["queued", "running", "stopping"].includes(run.status)) {
            await new Promise((resolve, reject) => {
              const aborted = () => {
                clearTimeout(timer);
                reject(exec.signal.reason);
              };
              const timer = setTimeout(() => {
                exec.signal?.removeEventListener("abort", aborted);
                resolve();
              }, 1000);
              exec.signal?.addEventListener("abort", aborted, { once: true });
              if (exec.signal?.aborted) aborted();
            });
            run = await request(
              "/api/runs/" + value.id,
              undefined,
              exec.signal,
            );
          }
          const previews = run.agent_observation?.data?.workflow?.previews ?? [];
          const attachments = [];
          const missed = [];
          for (const name of previews.slice(0, 4)) {
            const artifact = run.artifacts?.items?.find(row => row.ref === "output/" + name && row.type === "image");
            if (!artifact || !ctx.attachments) { missed.push(name); continue; }
            try {
              const data = await request(`/api/runs/${value.id}/artifacts/${artifact.id}`, undefined, exec.signal, true);
              const attachment = await ctx.attachments.saveImage({ data, mediaType: "image/jpeg", name });
              attachments.push({ name, attachment });
            } catch (error) {
              exec.signal?.throwIfAborted();
              missed.push(name);
            }
          }
          return {
            run_id: value.id,
            status: run.status,
            validation_status: run.state?.status ?? null,
            outcome: run.outcome ?? null,
            component: run.component ? { operation: run.component.operation, exports: run.component.exports,
              object_id: run.component.object_id ?? null, lineage: run.component.lineage ?? null } : null,
            agent_observation: run.agent_observation,
            ...(attachments.length ? { preview_attachments: attachments } : {}),
            ...(missed.length ? { previews_not_attached: missed } : {}),
            stage_errors: run.state?.error ?? null,
            artifacts: {
              items: run.artifacts?.items?.slice(0, 50) ?? [],
              total: run.artifacts?.items?.length ?? 0,
              truncated: run.artifacts?.truncated ?? false,
            },
            robot_execution: "not_run",
          };
        } finally {
          exec.signal?.removeEventListener("abort", abort);
          if (exec.signal?.aborted && longOperation(operation))
            await request("/api/native/cancel", identity).catch(() => {});
        }
      },
    });
}

export function apply(ctx, config) {
  registerTools(ctx, config);
  ctx.systemPrompt.section({
    name: "envcreator:studio",
    order: 130,
    text: () =>
      "You are the native DSH task agent in SceneCreator Studio. Use context, then workflow_start for a new single-object reconstruction. SceneCreator runs perception, modeling and verification in a fixed order. You decide what evidence and structure to use. For video, read the timestamped original contact sheet: choose the window, sampling rate, frame budget and a reference frame with the target visible. Name the view or motion you need when requesting a revisit. At target selection, match the actual mask/context and justify whether the new track is the same object; never match by category alone. Review the structure, its evidence and unknowns before accepting or revising the proposed hypothesis. Follow allowed_actions and revision, using the original workflow_id for advancement. Treat candidate renders as diagnostic only. If no new observation can answer the question, stop with the remaining uncertainty. Background and physics run only if explicitly requested. Read the actual validation scope; workflow completion, visual acceptance and physical function are separate. On interruption, resume without changing the task. Summarize useful evidence and decisions in Chinese. SceneCreator VLM credentials are independent of your task model. No robot operation is registered." +
      " On evidence revisit, retain useful original views: the workflow fuses raw observations after your explicit identity decision. If more than 16 distinct frames are available, use observation tickets to select the useful old/new views. For a separate build candidate you may use propose_build with explicitly assumed scale/limits; bind it to the canonical blueprint hash and actual view refs. Canonical unknowns stay unknown, relative units cannot become metres, and adopted assumptions must pass independent review, recipe conformance and the original geometry gates." +
      " For a screw cover or hinge with explicit design parameters, submit workflow_start with params.declared_design. Preserve user-specified dimensions and mechanics. Report the USD bundle as a candidate pending Isaac validation." +
      (config.debugComponentTools ? " Debug and legacy component tools are available for explicit component experiments." : ""),
  });
  const token = readFileSync(config.tokenFile, "utf8").trim();
  const origin = new URL(config.backendURL);
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: "prefix",
        path: API,
        handler: async (req, res) => {
          const rejection = ctx.connection.requestRejection(req);
          if (rejection !== undefined) {
            res.writeHead(rejection, { "Cache-Control": "no-store" });
            res.end();
            return;
          }
          const parsed = new URL(req.url, "http://localhost");
          const suffix = parsed.pathname.slice(API.length);
          // Native-only endpoints cannot be invoked by a browser to forge call identities.
          if (
            !/^\/(?:health|config|bind|selection|discover|telemetry|human\/action|tasks(?:\/[A-Za-z0-9_-]+(?:\/upload|\/workspace)?)?|runs\/[A-Za-z0-9_-]+(?:\/cancel|\/events|\/artifacts\/[a-f0-9]+)?)$/.test(
              suffix,
            )
          ) {
            res.writeHead(404);
            res.end();
            return;
          }
          const controller = new AbortController();
          res.on("close", () => {
            if (!res.writableEnded) controller.abort();
          });
          const headers = { authorization: "Bearer " + token };
          for (const key of ["content-type", "range", "if-range", "last-event-id"])
            if (req.headers[key]) headers[key] = req.headers[key];
          try {
            const upstream = await fetch(
              new URL("/api" + suffix + parsed.search, origin),
              {
                method: req.method,
                headers,
                signal: controller.signal,
                redirect: "error",
                ...(["GET", "HEAD"].includes(req.method)
                  ? {}
                  : { body: req, duplex: "half" }),
              },
            );
            res.statusCode = upstream.status;
            for (const key of [
              "content-type",
              "content-length",
              "content-range",
              "accept-ranges",
              "content-disposition",
              "last-modified",
              "etag",
            ]) {
              if (upstream.headers.has(key))
                res.setHeader(key, upstream.headers.get(key));
            }
            res.setHeader("Cache-Control", "no-store");
            res.setHeader("X-Content-Type-Options", "nosniff");
            if (upstream.body)
              await pipeline(Readable.fromWeb(upstream.body), res);
            else res.end();
          } catch {
            if (!res.headersSent) res.writeHead(502);
            if (!res.destroyed) res.end();
          }
        },
      }),
    "SceneCreator authenticated asset routes",
  );
}
