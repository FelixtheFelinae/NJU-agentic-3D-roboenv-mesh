import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  Layers3,
  Activity,
  FolderOpen,
  Upload,
  Settings2,
  Search,
  ChevronRight,
  Download,
  Columns2,
  Maximize2,
  X,
  Cpu,
  MemoryStick,
  HardDrive,
  RefreshCw,
  AlertTriangle,
  LoaderCircle,
  CheckCircle2,
  Clock3,
  Square,
  Image as ImageIcon,
  Terminal,
  Network,
  Sparkles,
} from "lucide-react";
import { ModelViewer } from "./model.jsx";
import { HumanWorkspace } from "./workspace.jsx";
import styles from "./style.css";
export const name = "envcreator-studio-client";
export const inject = [
  "connection",
  "slots",
  "uiConversation",
  "sessions",
  "uiWorkspace",
  "sidebarRight",
  "sidebarRightTabs",
];
const KIND = "envcreator.studio",
  TAB = "@envcreator/studio/workbench",
  base = "/envcreator/api";
const labels = {
  queued: "排队中",
  running: "运行中",
  stopping: "正在停止",
  cancelled: "已停止",
  failed: "执行失败",
  completed: "执行结束",
  verified: "流程通过",
  unverified: "未验证",
  review_passed: "审核通过",
  explicit_selection: "已指定范围",
  physics_passed: "物理协议通过",
  not_applicable: "本步不适用",
  historical: "历史实验",
  unknown: "状态待核对",
  interrupted: "已中断",
  timeout: "执行超时",
  not_run: "未执行",
};
const size = (n) =>
  n == null
    ? "—"
    : n >= 1024 ** 3
      ? (n / 1024 ** 3).toFixed(1) + " GB"
      : n >= 1024 ** 2
        ? (n / 1024 ** 2).toFixed(1) + " MB"
        : (n / 1024).toFixed(0) + " KB";
const num = (n) => (n == null ? "—" : Math.round(n));
const date = (t) =>
  t
    ? new Date(typeof t === "number" ? t * 1000 : t).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
async function api(path, body, signal) {
  const r = await fetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    ...(body === undefined
      ? {}
      : {
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }),
    signal,
  });
  if (!r.ok) {
    const v = await r.json().catch(() => ({}));
    throw Error(v.detail ?? "HTTP " + r.status);
  }
  return r.json();
}
const url = (run, item) => base + "/runs/" + run + "/artifacts/" + item.id;
function Status({ value }) {
  return (
    <span
      className={
        "ec-status " +
        (["verified"].includes(value)
          ? "good"
          : ["running", "queued", "stopping"].includes(value)
            ? "busy"
            : ["failed", "timeout"].includes(value)
              ? "bad"
              : value === "unverified"
                ? "warning"
                : "")
      }
    >
      <i className="ec-dot" />
      {labels[value] ?? value ?? "准备就绪"}
    </span>
  );
}
function Spark({ values }) {
  const v = values.filter((n) => n != null && Number.isFinite(n));
  const max = Math.max(100, ...v);
  return (
    <svg className="ec-chart" viewBox="0 0 240 46" preserveAspectRatio="none">
      <path d="M0 44 H240" stroke="#e8edf0" />
      {v.length > 1 && (
        <polyline
          points={v
            .map(
              (n, i) => `${(i / (v.length - 1)) * 240},${44 - (n / max) * 40}`,
            )
            .join(" ")}
          fill="none"
          stroke="#168b85"
          strokeWidth="1.8"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
function Monitor({ data }) {
  const l = data?.latest ?? {},
    h = data?.history ?? [];
  const metric = (title, Icon, value, unit, values, note) => (
    <div className="ec-metric" key={title}>
      <div className="ec-metric-top">
        <Icon size={14} />
        {title}
      </div>
      <div className="ec-metric-value">
        {value}
        <small>{unit}</small>
      </div>
      <Spark values={values} />
      <small>{note}</small>
    </div>
  );
  return (
    <div className="ec-monitor">
      <div>
        <div className="ec-tag">INFRASTRUCTURE</div>
        <h2>执行节点与资源</h2>
        <small>
          {l.host ?? "连接中"} · 约每 2 秒采样 · {date(l.at)}
        </small>
      </div>
      <div className="ec-monitor-grid">
        {metric(
          "CPU",
          Cpu,
          num(l.cpu_percent),
          "%",
          h.map((v) => v.cpu_percent),
          `${l.cpu_count ?? "—"} 个逻辑核心 · 整机占用`,
        )}
        {metric(
          "系统内存",
          MemoryStick,
          num(l.memory_percent),
          "%",
          h.map((v) => v.memory_percent),
          size(l.memory_used) + " / " + size(l.memory_total),
        )}
        {metric(
          "活跃作业",
          Layers3,
          l.active_jobs?.length ?? 0,
          "",
          h.map((v) => v.active_jobs?.length ?? 0),
          `${l.managed_process_count ?? 0} 个受管理进程`,
        )}
        {metric(
          "产物盘剩余",
          HardDrive,
          size(l.disk_free),
          "",
          [],
          `读 ${size(l.disk_read_bps)}/s · 写 ${size(l.disk_write_bps)}/s`,
        )}
      </div>
      <div className="ec-monitor-grid">
        {(l.gpus ?? []).map((g, i) =>
          metric(
            g.name,
            Cpu,
            num(g.utilization),
            "% GPU",
            h.map((v) => v.gpus?.[i]?.utilization),
            `GPU ${g.index} · 显存 ${num(g.memory_used_mb)} / ${num(g.memory_total_mb)} MB · ${num(g.temperature)} °C · ${num(g.power_w)} W`,
          ),
        )}
      </div>
      {l.gpu_error && (
        <div className="ec-notice">
          <AlertTriangle size={14} />
          {l.gpu_error}
        </div>
      )}
      <div className="ec-section">
        <div className="ec-section-head">
          <Network size={14} />
          进程与作业关联
          <span className="ec-spacer" />
          <small>系统总进程 {l.system_process_count ?? "—"}</small>
        </div>
        <table>
          <thead>
            <tr>
              <th>PID / 父进程</th>
              <th>名称</th>
              <th>CPU¹</th>
              <th>内存</th>
              <th>GPU 显存</th>
              <th>归属</th>
            </tr>
          </thead>
          <tbody>
            {(l.processes ?? []).map((p) => (
              <tr key={p.pid}>
                <td className="ec-mono">
                  {p.pid}
                  <small> / {p.ppid}</small>
                </td>
                <td>{p.name}</td>
                <td>{num(p.cpu_percent)}%</td>
                <td>{size(p.rss)}</td>
                <td>
                  {l.gpu_processes?.find((g) => g.pid === p.pid)?.memory_mb ??
                    "—"}{" "}
                  MB
                </td>
                <td>
                  {p.managed ? (
                    <span className="ec-status good">
                      {p.run_id?.slice(0, 8)}
                    </span>
                  ) : (
                    <small>只读观察</small>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="ec-note">
          ¹ 进程 CPU 按单核心计，可能超过
          100%。显示相关计算进程；取消仅作用于工作台管理的作业。
        </div>
      </div>
    </div>
  );
}
function Preview({ run, item }) {
  const [text, setText] = useState(""),
    [error, setError] = useState("");
  const link = item ? url(run, item) : "";
  useEffect(() => {
    setText("");
    setError("");
    if (!item || ["image", "input", "video", "model"].includes(item.kind))
      return;
    const c = new AbortController();
    fetch(link, { signal: c.signal })
      .then((r) => {
        if (!r.ok) throw Error("读取失败");
        return r.text();
      })
      .then(setText)
      .catch((e) => {
        if (!c.signal.aborted) setError(e.message);
      });
    return () => c.abort();
  }, [link]);
  if (!item)
    return (
      <div className="ec-empty">
        <div className="ec-empty-icon">
          <Box size={33} strokeWidth={1.2} />
        </div>
        <h2>让场景从这里开始</h2>
        <p>
          上传图片或视频，在原生对话中描述需求。每轮生成的模型、渲染和验证证据会显示在这里。
        </p>
        <small>也可从「实验记录」打开已有结果</small>
      </div>
    );
  const ext = item.name.split(".").at(-1).toLowerCase();
  return (
    <>
      <span className="ec-view-label">
        <i className="ec-dot" />
        {item.kind === "input"
          ? "真实输入"
          : item.kind === "image"
            ? "已有图像 · 实验性质见来源"
            : item.kind === "video"
              ? "已有视频 · 验证条件见报告"
              : item.kind === "model"
                ? "资产预览"
                : "原始记录 · 脱敏显示"}
      </span>
      {["input", "image"].includes(item.kind) ? (
        <img src={link} alt={item.ref} />
      ) : item.kind === "video" ? (
        <video src={link} controls />
      ) : item.kind === "model" && !["usd", "usda", "usdc"].includes(ext) ? (
        <ModelViewer url={link} name={item.name} type={item.type} />
      ) : item.kind === "model" ? (
        <div className="ec-empty">
          <Box size={28} />
          <h2>USD 原始资产</h2>
          <p>
            浏览器支持 GLB、OBJ、STL、PLY。可查看同轮渲染或下载
            USD；尚未生成预览转换。
          </p>
        </div>
      ) : (
        <pre>{error || text || "正在读取记录…"}</pre>
      )}
    </>
  );
}
function Configuration({ config }) {
  return (
    <div className="ec-settings">
      <div>
        <div className="ec-tag">NATIVE CONFIGURATION</div>
        <h2>工作台与执行环境</h2>
      </div>
      <div className="ec-section">
        <div className="ec-section-head">
          <Sparkles size={15} />
          原生 DSH 模型
        </div>
        <dl>
          <dt>任务模型</dt>
          <dd>{config?.model?.model ?? "尚未配置"}</dd>
          <dt>协议</dt>
          <dd>{config?.model?.api_protocol ?? "—"}</dd>
          <dt>凭据</dt>
          <dd>独立 DSH 配置 · 后端保存</dd>
          <dt>VLM</dt>
          <dd>仅由 EnvCreator 内部配置使用</dd>
        </dl>
        <div className="ec-note">
          沿用 DSH 原生模型设置；部署由 Studio TOML 与独立模型文件配置。
        </div>
      </div>
      <div className="ec-section">
        <div className="ec-section-head">
          <Settings2 size={15} />
          生成环境
        </div>
        <dl>
          {Object.entries(config?.settings ?? {})
            .filter(
              ([k]) =>
                !["dsh_root", "state_root", "import_root", "model"].includes(k),
            )
            .map(([k, v]) => (
              <React.Fragment key={k}>
                <dt>{k}</dt>
                <dd>{String(v ?? "未配置")}</dd>
              </React.Fragment>
            ))}
        </dl>
      </div>
      <div className="ec-notice">
        <AlertTriangle size={14} />
        {config?.generation_missing?.length
          ? "生成前需配置：" + config.generation_missing.join("、")
          : "生成环境路径齐备；实际调用和物理结果以运行证据为准。"}
      </div>
    </div>
  );
}
function createWorkbench(ctx) {
  return function Workbench({ sessionId }) {
    const [page, setPage] = useState("workbench"),
      [technical, setTechnical] = useState(false),
      [tasks, setTasks] = useState([]),
      [task, setTask] = useState(null),
      [config, setConfig] = useState(null),
      [runId, setRunId] = useState(""),
      [run, setRun] = useState(null),
      [telemetry, setTelemetry] = useState(null),
      [tab, setTab] = useState("image"),
      [selected, setSelected] = useState(""),
      [drawer, setDrawer] = useState(false),
      [search, setSearch] = useState(""),
      [error, setError] = useState(""),
      [uploading, setUploading] = useState(false),
      [compare, setCompare] = useState(false),
      [otherId, setOtherId] = useState(""),
      [other, setOther] = useState(null),
      [otherArtifact, setOtherArtifact] = useState(""),
      [detail, setDetail] = useState(null);
    const root = useRef(null),
      file = useRef(null),
      currentTask = useRef(null),
      runSelection = useRef("");
    useEffect(() => {
      let dead = false;
      const c = new AbortController();
      setTask(null);
      setTechnical(false);
      setRun(null);
      setRunId("");
      runSelection.current = "";
      currentTask.current = null;
      Promise.all([
        api("/bind", { session_id: sessionId }, c.signal),
        api("/tasks", undefined, c.signal),
        api("/config", undefined, c.signal),
      ])
        .then(([bound, list, cfg]) => {
          if (dead) return;
          setTask(bound);
          currentTask.current = bound.id;
          setTasks(list);
          setConfig(cfg);
          setRunId(bound.runs[0]?.id ?? "");
        })
        .catch((e) => {
          if (!dead) setError(e.message);
        });
      return () => {
        dead = true;
        c.abort();
      };
    }, [sessionId]);
    useEffect(() => {
      let dead = false,
        busy = false;
      const c = new AbortController();
      async function poll() {
        if (busy) return;
        busy = true;
        try {
          const d = await api("/telemetry", undefined, c.signal);
          if (!dead) setTelemetry(d);
          if (currentTask.current) {
            const b = await api(
              "/tasks/" + currentTask.current,
              undefined,
              c.signal,
            );
            if (!dead) {
              setTask(b);
              if (!runSelection.current && b.runs[0]) setRunId(b.runs[0].id);
            }
          }
        } catch (e) {
          if (!dead) setError(e.message);
        } finally {
          busy = false;
        }
      }
      poll();
      const t = setInterval(poll, 2500);
      return () => {
        dead = true;
        c.abort();
        clearInterval(t);
      };
    }, [sessionId]);
    useEffect(() => {
      setRun(null);
      if (!runId) {
        return;
      }
      let dead = false,
        busy = false;
      const c = new AbortController();
      async function load() {
        if (busy) return;
        busy = true;
        try {
          const d = await api("/runs/" + runId, undefined, c.signal);
          if (!dead) {
            setRun(d);
          }
        } catch (e) {
          if (!dead) setError(e.message);
        } finally {
          busy = false;
        }
      }
      load();
      const t = setInterval(load, 3000);
      return () => {
        dead = true;
        c.abort();
        clearInterval(t);
      };
    }, [runId]);
    useEffect(() => {
      setOther(null);
      if (!otherId) {
        return;
      }
      const c = new AbortController();
      api("/runs/" + otherId, undefined, c.signal)
        .then(setOther)
        .catch((e) => {
          if (!c.signal.aborted) setError(e.message);
        });
      return () => c.abort();
    }, [otherId]);
    const items = run?.artifacts?.items ?? [],
      filtered = items.filter((v) =>
        tab === "image"
          ? ["image", "video"].includes(v.kind)
          : tab === "input"
            ? v.kind === "input"
            : tab === "model"
              ? v.kind === "model"
              : tab === "evidence"
                ? v.kind === "evidence"
                : ["log", "data"].includes(v.kind),
      );
    const item =
      filtered.find((v) => v.id === selected) ??
      filtered.find((v) => v.name.endsWith(".glb")) ??
      filtered.find((v) => v.ref.includes("final")) ??
      filtered.at(-1);
    const alternatives = (other?.artifacts?.items ?? []).filter(
      (v) => v.kind === item?.kind,
    );
    const otherItem =
      alternatives.find((v) => v.id === otherArtifact) ?? alternatives.at(-1);
    async function chooseTask(id) {
      try {
        const b = await api("/bind", { session_id: sessionId, task_id: id });
        setTask(b);
        currentTask.current = id;
        runSelection.current = "";
        setRunId(b.runs[0]?.id ?? "");
        setSelected("");
        setDrawer(false);
        setPage("workbench");
      } catch (e) {
        setError(e.message);
      }
    }
    async function upload(files) {
      if (!task) return;
      setUploading(true);
      try {
        for (const f of files) {
          const form = new FormData();
          form.append("file", f);
          const r = await fetch(base + "/tasks/" + task.id + "/upload", {
            method: "POST",
            body: form,
          });
          const d = await r.json();
          if (!r.ok) throw Error(d.detail ?? "上传失败");
        }
        setTask(await api("/tasks/" + task.id));
      } catch (e) {
        setError(e.message);
      } finally {
        setUploading(false);
      }
    }
    async function newSession() {
      try {
        const r = await ctx.connection.rpc.call("/api", "session/create", {
          args: {
            request: {
              agentPreset: "envcreator-studio",
              cwd: config?.settings?.state_root + "/workspace",
            },
          },
        });
        if (!r.ok) throw Error(r.error.message);
        if (config?.model?.provider && config?.model?.model) {
          const selection = await ctx.connection.rpc.call(
            "/api",
            "session/selectModel",
            {
              args: {
                request: {
                  sessionId: r.value.sessionId,
                  provider: config.model.provider,
                  model: config.model.model,
                },
              },
            },
          );
          if (!selection.ok) throw Error(selection.error.message);
        }
        ctx.uiWorkspace.openSession(r.value.sessionId);
        setDrawer(false);
      } catch (e) {
        setError(e.message);
      }
    }
    const l = telemetry?.latest ?? {},
      g = l.gpus?.[0],
      stale = !l.at || Date.now() / 1000 - l.at > 10;
    return (
      <div className="ec-app" ref={root}>
        <style>{styles}</style>
        <header className="ec-top">
          <span className="ec-logo">
            <Layers3 size={20} />
          </span>
          <div>
            <div className="ec-brand">
              EnvCreator <span className="ec-tag">STUDIO</span>
            </div>
            <div className="ec-breadcrumb">资产生成 / 原生 DSH 工作台</div>
          </div>
          <span className="ec-spacer" />
          <div
            className="ec-connection"
            style={stale ? { color: "#c18e54" } : {}}
          >
            <i className="ec-dot" />
            <span>{stale ? "遥测待更新" : l.host}</span>
          </div>
          <button
            title="实验记录"
            onClick={() => {
              setDrawer(true);
              api("/tasks")
                .then(setTasks)
                .catch((e) => setError(e.message));
            }}
          >
            <FolderOpen size={17} />
          </button>
          <button title="环境设置" onClick={() => setPage("settings")}>
            <Settings2 size={17} />
          </button>
        </header>
        <nav className="ec-nav">
          {[
            ["workbench", "生成工作台", Box],
            ["monitor", "运行监控", Activity],
            ["settings", "环境设置", Settings2],
          ].map(([id, label, Icon]) => (
            <button
              key={id}
              className={page === id ? "active" : ""}
              onClick={() => setPage(id)}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </nav>
        {error && (
          <div className="ec-notice ec-error" role="alert">
            <AlertTriangle size={14} />
            <span>{error}</span>
            <span className="ec-spacer" />
            <button onClick={() => setError("")}>
              <X size={12} />
            </button>
          </div>
        )}
        {page === "monitor" ? (
          <Monitor data={telemetry} />
        ) : page === "settings" ? (
          <Configuration config={config} />
        ) : (
          <>
            <div className="ec-task-head">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="ec-task-title">
                  {task?.title ?? "正在连接原生会话…"}
                </div>
                <div className="ec-breadcrumb">
                  {runId
                    ? runId + " · " + date(run?.created)
                    : "描述需求，开始一次可追溯的还原"}
                </div>
              </div>
              <Status value={run?.status} />
              {!!run?.outcome?.validation_status && (
                <Status value={run.outcome.validation_status} />
              )}
              <button
                title="全屏工作台"
                onClick={() =>
                  document.fullscreenElement
                    ? document.exitFullscreen()
                    : root.current?.requestFullscreen()
                }
              >
                <Maximize2 size={14} />
              </button>
            </div>
            {!technical ? <HumanWorkspace ctx={ctx} sessionId={sessionId} task={task} runId={runId}
              onRun={(id, pin = true) => {setRunId(id);runSelection.current = pin ? id : "";}}
              onUpload={upload} onTechnical={() => setTechnical(true)} /> : <div className="ec-content">
              <button onClick={() => setTechnical(false)}><ChevronRight size={14}/>返回交互工作区</button>
              {!!run?.outcome?.validation_status && (
                <div className="ec-outcome">
                  <div className="ec-outcome-summary">
                    <span>Harness 结果</span>
                    <Status value={run.outcome.validation_status} />
                    {run.outcome.physics_status && (
                      <span>
                        物理验证：
                        {labels[run.outcome.physics_status] ??
                          run.outcome.physics_status}
                      </span>
                    )}
                    {!!run.outcome.blocked_assets && (
                      <span>{run.outcome.blocked_assets} 项资产被阻塞</span>
                    )}
                  </div>
                  {run.outcome.blockers?.map((blocker, index) => (
                    <div className="ec-notice" key={index}>
                      <AlertTriangle size={14} />
                      <span>{blocker.reason}</span>
                      {items.some((v) => v.ref === blocker.report_ref) && (
                        <button
                          onClick={() => {
                            setCompare(false);
                            setTab("evidence");
                            setSelected(
                              items.find((v) => v.ref === blocker.report_ref)
                                .id,
                            );
                          }}
                        >
                          查看原始审核
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {!!run?.imported && (
                <div className="ec-notice">
                  <Clock3 size={14} />
                  历史实验：展示已有文件与快照；缺失调用记录未补写。
                </div>
              )}
              <div className="ec-tabs">
                {[
                  ["image", "图像 / 渲染", ImageIcon],
                  ["input", "输入", Layers3],
                  ["model", "三维模型", Box],
                  ["evidence", "证据 / 验证", CheckCircle2],
                  ["log", "记录", Terminal],
                ].map(([id, label, Icon]) => (
                  <button
                    className={tab === id ? "active" : ""}
                    key={id}
                    onClick={() => {
                      setTab(id);
                      setSelected("");
                    }}
                  >
                    <Icon size={12} />
                    {label}
                  </button>
                ))}
                <select
                  aria-label="运行版本"
                  value={runId}
                  onChange={(e) => {
                    setRunId(e.target.value);
                    runSelection.current = e.target.value;
                    setSelected("");
                  }}
                >
                  <option value="">选择运行</option>
                  {task?.runs.map((v, i) => (
                    <option key={v.id} value={v.id}>
                      {task.runs.length - i} · {date(v.created)} ·{" "}
                      {labels[v.status] ?? v.status}
                    </option>
                  ))}
                </select>
              </div>
              {compare ? (
                <>
                  <div className="ec-artifact-bar">
                    <select
                      aria-label="对比运行"
                      value={otherId}
                      onChange={(e) => setOtherId(e.target.value)}
                    >
                      <option value="">选择另一轮运行</option>
                      {tasks
                        .filter((t) => t.latest_run)
                        .map((t) => (
                          <option value={t.latest_run} key={t.id}>
                            {t.title}
                          </option>
                        ))}
                      {task?.runs
                        .filter((r) => r.id !== runId)
                        .map((r) => (
                          <option value={r.id} key={r.id}>
                            {r.id} · {date(r.created)}
                          </option>
                        ))}
                    </select>
                    <button onClick={() => setCompare(false)}>
                      <X size={13} />
                      结束对比
                    </button>
                  </div>
                  <div className="ec-compare">
                    <div className="ec-viewer">
                      <Preview run={runId} item={item} />
                    </div>
                    <div className="ec-viewer">
                      <Preview run={otherId} item={otherItem} />
                    </div>
                  </div>
                  {alternatives.length > 0 && (
                    <select
                      aria-label="对比产物"
                      value={otherItem?.id ?? ""}
                      onChange={(e) => setOtherArtifact(e.target.value)}
                    >
                      {alternatives.map((v) => (
                        <option value={v.id} key={v.id}>
                          {v.ref}
                        </option>
                      ))}
                    </select>
                  )}
                  <small>
                    原始产物对比；视角独立操作，单位与验证结论以报告为准。
                  </small>
                </>
              ) : (
                <div className="ec-viewer">
                  <Preview run={runId} item={item} />
                </div>
              )}
              <div className="ec-artifact-bar">
                <select
                  aria-label="选择产物"
                  value={item?.id ?? ""}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  <option value="">
                    {filtered.length ? "选择产物" : "当前分类暂无产物"}
                  </option>
                  {filtered.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.ref}
                    </option>
                  ))}
                </select>
                <button
                  title="对比版本"
                  onClick={() => setCompare(!compare)}
                  disabled={!run}
                >
                  <Columns2 size={14} />
                  <span>对比</span>
                </button>
                <button
                  title="来源与哈希"
                  onClick={() =>
                    item &&
                    api(
                      "/runs/" +
                        runId +
                        "/artifacts/" +
                        item.id +
                        "?metadata=true",
                    )
                      .then(setDetail)
                      .catch((e) => setError(e.message))
                  }
                  disabled={!item}
                >
                  <Layers3 size={14} />
                </button>
                {item && (
                  <a
                    href={url(runId, item)}
                    download={item.name}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <button title="下载产物">
                      <Download size={14} />
                    </button>
                  </a>
                )}
              </div>
              {tab === "image" && (
                <div className="ec-strip">
                  {filtered
                    .filter((v) => v.kind === "image")
                    .slice(-24)
                    .map((v) => (
                      <button
                        key={v.id}
                        title={v.ref}
                        className={
                          "ec-thumbnail " +
                          (v.id === item?.id ? "selected" : "")
                        }
                        onClick={() => setSelected(v.id)}
                      >
                        <img loading="lazy" src={url(runId, v)} alt={v.name} />
                        <span>{v.ref.split("/").slice(-2).join("/")}</span>
                      </button>
                    ))}
                </div>
              )}
              <div className="ec-section">
                <div className="ec-section-head">
                  <Activity size={14} />
                  执行时间线
                  <span className="ec-spacer" />
                  <small>{run?.events?.length ?? 0} 条记录</small>
                  {["running", "queued"].includes(run?.status) &&
                    !run?.imported && (
                      <button
                        className="ec-danger"
                        onClick={() =>
                          api("/runs/" + runId + "/cancel", {}).catch((e) =>
                            setError(e.message),
                          )
                        }
                      >
                        <Square size={10} />
                        停止生成
                      </button>
                    )}
                </div>
                <div className="ec-timeline">
                  {run?.events?.length ? (
                    run.events
                      .slice(-35)
                      .reverse()
                      .map((e, i) => (
                        <div className={"ec-event " + e.event} key={i}>
                          <i className="ec-dot" />
                          <time>{e.at ? date(e.at) : "历史"}</time>
                          <details>
                            <summary>
                              {e.name ?? e.operation ?? "工具调用"}{" "}
                              <small>
                                {{
                                  started: "执行中",
                                  completed: "完成",
                                  failed: "失败",
                                  queued: "等待资源",
                                }[e.event] ?? e.event}
                                {e.elapsed_seconds != null
                                  ? " · " + e.elapsed_seconds.toFixed(1) + "s"
                                  : ""}
                              </small>
                            </summary>
                            <pre>{JSON.stringify(e, null, 2)}</pre>
                          </details>
                        </div>
                      ))
                  ) : (
                    <div className="ec-blank-light">
                      调用发生后，阶段、耗时、失败和产物将在这里关联。
                    </div>
                  )}
                </div>
              </div>
              <div className="ec-upload">
                <Upload size={19} color="#78958f" />
                <div className="ec-upload-copy">
                  <strong>添加本轮视觉输入</strong>
                  <p>图片 / 单段视频 · 随后在原生对话中描述需求</p>
                </div>
                <button
                  disabled={!task || uploading}
                  onClick={() => file.current?.click()}
                >
                  {uploading ? (
                    <LoaderCircle className="ec-spin" size={13} />
                  ) : (
                    <Upload size={13} />
                  )}
                  上传
                </button>
                <input
                  ref={file}
                  hidden
                  type="file"
                  multiple
                  accept="image/png,image/jpeg,image/webp,video/*"
                  onChange={(e) => {
                    upload([...e.target.files]);
                    e.target.value = "";
                  }}
                />
              </div>
              {task?.inputs?.length > 0 && (
                <div className="ec-input-chips">
                  {task.inputs.map((v) => (
                    <span
                      title={"输入 ID: " + v.id + " · SHA256: " + v.sha256}
                      className="ec-chip"
                      key={v.id}
                    >
                      {v.name} · {size(v.size)}
                    </span>
                  ))}
                </div>
              )}
              {!run && (
                <small>
                  上传后可说：“读取本任务输入，生成还原结果并保留验证证据。”
                </small>
              )}
            </div>}
          </>
        )}
        <footer className="ec-resource-bar">
          <span>
            <Cpu size={12} />
            GPU <b>{g ? num(g.utilization) + "%" : "—"}</b>
          </span>
          <span>
            显存{" "}
            <b>
              {g
                ? (g.memory_used_mb / 1024).toFixed(1) +
                  " / " +
                  (g.memory_total_mb / 1024).toFixed(0) +
                  " GB"
                : "未知"}
            </b>
          </span>
          <span>
            CPU <b>{num(l.cpu_percent)}%</b>
          </span>
          <span>
            RAM <b>{size(l.memory_used)}</b>
          </span>
          <span>
            作业 <b>{l.active_jobs?.length ?? 0}</b> / 进程{" "}
            <b>{l.managed_process_count ?? 0}</b>
          </span>
          <button title="查看资源详情" onClick={() => setPage("monitor")}>
            <ChevronRight size={12} />
          </button>
        </footer>
        {drawer && (
          <div className="ec-overlay" onClick={() => setDrawer(false)}>
            <aside className="ec-drawer" onClick={(e) => e.stopPropagation()}>
              <div className="ec-drawer-head">
                <FolderOpen size={17} />
                <h2>实验记录</h2>
                <span className="ec-spacer" />
                <button title="关闭记录" onClick={() => setDrawer(false)}>
                  <X size={15} />
                </button>
              </div>
              <div style={{ display: "flex", gap: 7, padding: "12px 12px 0" }}>
                <button className="ec-primary" onClick={newSession}>
                  <Sparkles size={13} />
                  新任务 / 原生会话
                </button>
                <button
                  title="重新发现历史实验"
                  onClick={() =>
                    api("/discover", {})
                      .then(setTasks)
                      .catch((e) => setError(e.message))
                  }
                >
                  <RefreshCw size={13} />
                </button>
              </div>
              <div className="ec-filter">
                <Search size={14} />
                <input
                  aria-label="搜索实验"
                  placeholder="搜索任务、实验目录…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="ec-task-list">
                {tasks
                  .filter((t) =>
                    t.title.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((t) => (
                    <button
                      className="ec-task-item"
                      key={t.id}
                      onClick={() => chooseTask(t.id)}
                    >
                      <strong>{t.title}</strong>
                      <small>
                        <span>
                          {date(t.created)} · {t.run_count} 轮
                        </span>
                        <Status value={t.status} />
                      </small>
                    </button>
                  ))}
              </div>
            </aside>
          </div>
        )}
        {detail && (
          <div className="ec-overlay" onClick={() => setDetail(null)}>
            <div className="ec-drawer" onClick={(e) => e.stopPropagation()}>
              <div className="ec-drawer-head">
                <Layers3 size={16} />
                <h2>产物来源</h2>
                <span className="ec-spacer" />
                <button onClick={() => setDetail(null)}>
                  <X size={14} />
                </button>
              </div>
              <div className="ec-modal-body">
                <p>{detail.ref}</p>
                <small>
                  {size(detail.size)} · {date(detail.modified)}
                </small>
                <h3 style={{ marginTop: 18, fontSize: 12 }}>SHA-256</h3>
                <p
                  className="ec-mono"
                  style={{
                    fontSize: 11,
                    overflowWrap: "anywhere",
                    marginTop: 8,
                  }}
                >
                  {detail.sha256}
                </p>
                <p style={{ fontSize: 11, marginTop: 20, color: "#83919d" }}>
                  哈希说明文件身份。功能验证需核对绑定该文件的协议和报告。
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };
}
export function apply(ctx) {
  const Workbench = createWorkbench(ctx);
  const Button = () => (
    <button
      title="打开 EnvCreator 工作台"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        border: "1px solid #d6e4df",
        borderRadius: 7,
        padding: "4px 9px",
        fontSize: 12,
        background: "#edf6f2",
        color: "#277e71",
        cursor: "pointer",
      }}
      onClick={() => ctx.sidebarRight.openTab(KIND)}
    >
      <Layers3 size={14} />
      EnvCreator
    </button>
  );
  ctx.effect(() => {
    const cleanup = [];
    cleanup.push(
      ctx.sidebarRightTabs.register({
        id: TAB,
        kind: KIND,
        title: () => "EnvCreator Studio",
        guide: [
          {
            id: KIND,
            order: 10,
            title: () => "EnvCreator Studio",
            description: () => "资产、渲染、证据与实时负载",
            icon: () => <Layers3 size={20} />,
          },
        ],
      }),
    );
    cleanup.push(
      ctx.slots.register(
        {
          name: "sidebar.right.pane.tab",
          key: TAB,
          registrant: "@envcreator/studio",
        },
        Workbench,
      ),
    );
    cleanup.push(
      ctx.slots.inject("conversation.session.header.utilities", () =>
        ctx.slots.register(
          {
            name: "conversation.session.header.utilities",
            id: "envcreator.open",
            order: 15,
            registrant: "@envcreator/studio",
          },
          Button,
        ),
      ),
    );
    cleanup.push(
      ctx.slots.inject("conversation.view", () =>
        ctx.slots.register(
          {
            name: "conversation.view",
            id: "envcreator.workbench",
            order: 15,
            label: "EnvCreator",
            registrant: "@envcreator/studio",
          },
          Workbench,
        ),
      ),
    );
    cleanup.push(
      ctx.slots.inject("settings.plugins.tab", () =>
        ctx.slots.register(
          {
            name: "settings.plugins.tab",
            id: "envcreator",
            label: "EnvCreator",
            order: 35,
            registrant: "@envcreator/studio",
          },
          () => (
            <div style={{ padding: 16, fontSize: 13 }}>
              EnvCreator 使用 Studio TOML 与 DSH
              原生模型设置。产物、调用和资源在工作台中查看。
            </div>
          ),
        ),
      ),
    );
    let settled = false,
      timer;
    const initialize = async () => {
      if (settled) return;
      const snapshot = ctx.sessions.list.getSnapshot();
      if (snapshot.phase !== "ready") return;
      settled = true;
      const current = Object.values(snapshot.byId).find(
        (v) => (v.retainedBy.mainView ?? 0) > 0,
      );
      try {
        if (!current) {
          const r = await ctx.connection.rpc.call("/api", "session/list", {
            args: { _request: {} },
          });
          if (r.ok) {
            const row =
              r.value.items?.find(
                (v) => v.agentPreset === "envcreator-studio",
              ) ?? r.value.items?.[0];
            if (row) ctx.uiWorkspace.openSession(row.sessionId);
          }
        }
        timer = setTimeout(() => {
          try {
            ctx.sidebarRight.openTab(KIND);
          } catch {}
        }, 300);
      } catch {}
    };
    cleanup.push(ctx.sessions.list.subscribe(initialize));
    initialize();
    return () => {
      clearTimeout(timer);
      for (const dispose of cleanup.reverse()) dispose();
    };
  }, "EnvCreator native workbench views");
}
