import React, {useEffect, useRef, useState} from "react";
import {Box, Image as ImageIcon, Layers3, Play, Upload, GitBranch, CheckCircle2, ArrowRight, RefreshCw, Square, Send, ChevronDown, Download, AlertTriangle} from "lucide-react";
import {ModelViewer} from "./model.jsx";

const base = "/envcreator/api";
const link = item => item ? `${base}/runs/${item.run_id}/artifacts/${item.id}` : "";
async function request(path, body, signal) {
  const response = await fetch(base + path, {method: body === undefined ? "GET" : "POST", credentials: "same-origin", signal,
    ...(body === undefined ? {} : {headers: {"content-type":"application/json"}, body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw Error(data.detail ?? "操作未能完成");
  return data;
}
const COLORS = ["#22a6a0", "#eea04b", "#b18ce0", "#659ee6", "#e37c90", "#86ab50"];
const STATUS = {running:"正在处理",queued:"等待运行",completed:"处理结束",failed:"未能完成",cancelled:"已停止",timeout:"运行超时",interrupted:"已中断",unverified:"未验证",verified:"流程通过",historical:"历史结果"};

function Empty({title, children, action}) {
  return <div className="ec-human-empty"><Layers3 size={36} strokeWidth={1.1}/><h2>{title}</h2><p>{children}</p>{action}</div>;
}

function Masks({frame, selected, onSelect}) {
  const canvas = useRef(null), image = useRef(null), buffers = useRef([]);
  const [opacity,setOpacity] = useState(.42), [ready,setReady] = useState(0), [error,setError] = useState("");
  useEffect(()=>{
    let dead=false;
    buffers.current=[];setError("");
    async function load() {
      const results = await Promise.all((frame.objects??[]).map(async(obj,index)=>{
        if (!obj.mask) return null;
        const mask = new Image(); mask.src = link(obj.mask); await mask.decode();
        if (dead) return null;
        const temp=document.createElement("canvas");temp.width=mask.naturalWidth;temp.height=mask.naturalHeight;
        const ctx=temp.getContext("2d",{willReadFrequently:true});ctx.drawImage(mask,0,0);
        const pixels=ctx.getImageData(0,0,temp.width,temp.height), bitmap=new Uint8Array(temp.width*temp.height);
        const color=COLORS[index%COLORS.length].slice(1).match(/../g).map(v=>parseInt(v,16));
        for(let i=0;i<bitmap.length;i++){bitmap[i]=pixels.data[i*4]>127?1:0;pixels.data[i*4]=color[0];pixels.data[i*4+1]=color[1];pixels.data[i*4+2]=color[2];pixels.data[i*4+3]=bitmap[i]?255:0;}
        ctx.putImageData(pixels,0,0);
        return {id:obj.id,canvas:temp,bitmap,width:temp.width,height:temp.height};
      }));
      if(!dead){buffers.current=results.filter(Boolean);setReady(n=>n+1);}
    }
    load().catch(()=>{if(!dead)setError("分割叠加暂不可用，可查看原图并重试。");});
    return ()=>{dead=true;buffers.current=[];};
  },[frame.id, frame.image?.id, JSON.stringify((frame.objects??[]).map(o=>[o.id,o.mask?.id,o.mask?.run_id]))]);
  useEffect(()=>{
    const surface=canvas.current, picture=image.current;
    if(!surface||!picture?.naturalWidth)return;
    surface.width=picture.naturalWidth;surface.height=picture.naturalHeight;
    const ctx=surface.getContext("2d");ctx.clearRect(0,0,surface.width,surface.height);
    for(const entry of buffers.current){
      if(entry.width!==surface.width||entry.height!==surface.height){setError("分割与原图尺寸不一致，未叠加显示。");continue;}
      ctx.globalAlpha=selected.length&&!selected.includes(entry.id)?opacity*.3:opacity;
      ctx.drawImage(entry.canvas,0,0);
    }
    ctx.globalAlpha=1;
    for(const obj of frame.objects??[]){if(!selected.includes(obj.id)||!obj.bbox)continue;
      const [x0,y0,x1,y1]=obj.bbox;ctx.strokeStyle="#fff";ctx.lineWidth=Math.max(2,surface.width/400);ctx.setLineDash([8,5]);ctx.strokeRect(x0,y0,x1-x0,y1-y0);}
  },[frame,selected,opacity,ready]);
  const hit=e=>{
    const rect=canvas.current.getBoundingClientRect(),x=Math.floor((e.clientX-rect.left)/rect.width*canvas.current.width),y=Math.floor((e.clientY-rect.top)/rect.height*canvas.current.height);
    const hits=buffers.current.filter(b=>b.bitmap[y*b.width+x]);
    // Prefer the smaller visible region when a whole-object and part mask overlap.
    hits.sort((a,b)=>{const box=id=>(frame.objects.find(o=>o.id===id)?.bbox??[0,0,1e6,1e6]);const area=b=>{const [x,y,X,Y]=box(b.id);return(X-x)*(Y-y);};return area(a)-area(b);});
    if(hits[0])onSelect(hits[0].id,e.shiftKey);
  };
  return <div className="ec-mask-section">
    <div className="ec-mask-stage"><img ref={image} src={link(frame.image)} alt="当前输入" onLoad={()=>setReady(n=>n+1)}/><canvas ref={canvas} onClick={hit} aria-label="选择分割区域"/></div>
    <div className="ec-mask-toolbar"><span>分割透明度</span><input aria-label="分割透明度" type="range" min="0" max=".8" step=".05" value={opacity} onChange={e=>setOpacity(Number(e.target.value))}/><small>点击选择 · Shift 多选</small></div>
    {error&&<small role="alert">{error}</small>}
  </div>;
}

function Structure({data,onAsk}){
  const [selected,setSelected]=useState("");
  if(!data)return <Empty title="等待结构方案">确定重建主体后，会在这里展示部件与活动关系。</Empty>;
  const depth=id=>{let n=0,p=data.parts.find(v=>v.id===id),seen=new Set();while(p?.parent&&!seen.has(p.id)){seen.add(p.id);n++;p=data.parts.find(v=>v.id===p.parent);}return n;};
  const current=data.parts.find(p=>p.id===selected)??data.parts[0];
  const joint=data.joints.find(j=>j.child===current?.id);
  return <div className="ec-structure"><div className="ec-view-note">{data.message}</div>
    <div className="ec-structure-map" aria-label="部件结构示意">{data.parts.map(p=><button key={p.id} style={{marginLeft:Math.min(depth(p.id),5)*24}} className={current?.id===p.id?"selected":""} onClick={()=>setSelected(p.id)}><Box size={18}/><span><strong>{p.name}</strong><small>{data.joints.find(j=>j.child===p.id)?.kind??"主体"}</small></span>{p.parent&&<GitBranch size={14}/>}</button>)}</div>
    {current&&<div className="ec-human-card"><strong>{current.role}</strong><p>{joint?`${joint.kind}连接 · ${joint.origin??"来源未知"}`:"对象主体"}</p>{joint?.axis&&<small>运动轴：{joint.axis.join("，")}</small>}
      <div className="ec-dimensions">{Object.entries(current.dimensions??{}).map(([axis,q])=><span key={axis}>{axis.toUpperCase()} {q.value==null?"未知":q.unit==="relative_width"?`${q.value.toFixed(3)} ×机身宽度`:`${(q.value*100).toFixed(1)} cm`}<small>{({observed:"观测",measured:"用户测量",assumed:"假设",estimated:"估计",unknown:"未知"})[q.source]??""}</small></span>)}</div>
      <div className="ec-human-actions"><button onClick={()=>onAsk(`请修改当前对象的 ${current.name}，这里需要可以活动的连接；先根据已有证据检查适合的关节。`)}>这里需要活动</button><button onClick={()=>onAsk(`请把当前对象的 ${current.name} 改为固定部件，保留外观并检查相关结构。`)}>保持固定</button></div>
    </div>}
    <small>{data.assumption_count} 项结构假设 · {data.unresolved_count} 项待确认。可直接在对话中说明尺寸或连接方式。</small>
  </div>;
}

function Asset({data,onAsk,reference:comparison}){
  const [mode,setMode]=useState("render"),[selected,setSelected]=useState(""),[part,setPart]=useState("");
  if(!data)return <Empty title="等待模型生成">构建完成后显示实际模型与渲染，可继续用自然语言修改。</Empty>;
  const render=data.renders.find(v=>v.id===selected)??data.renders[0];
  const objects=data.meshes.map(v=>({...v,url:link(v)}));
  const parts=[...new Set(data.meshes.map(v=>v.part).filter(Boolean))];
  return <div className="ec-asset-section"><div className="ec-human-actions"><button className={mode==="render"?"selected":""} onClick={()=>setMode("render")}>渲染图</button><button className={mode==="model"?"selected":""} onClick={()=>setMode("model")} disabled={!objects.length}>三维模型</button>{data.asset&&<a href={link(data.asset)} download><Download size={14}/>下载资产</a>}</div>
    {mode==="render"?<div className={comparison?"ec-human-compare":""}><div className="ec-render-frame">{render?<img src={link(render)} alt="真实模型渲染"/>:<Empty title="暂无渲染">可请求重新渲染。</Empty>}</div>{comparison&&<div className="ec-render-frame"><img src={link(comparison)} alt="对比版本渲染"/></div>}</div>:<div className="ec-human-model"><ModelViewer objects={objects} name="assembled.obj" selectedPart={part}/></div>}
    <div className="ec-human-thumbs">{data.renders.map(v=><button key={v.id} className={v.id===render?.id?"selected":""} onClick={()=>setSelected(v.id)}><img src={link(v)} alt="选择渲染视角"/></button>)}</div>
    {mode==="model"&&parts.length>1&&<select aria-label="高亮部件" value={part} onChange={e=>setPart(e.target.value)}><option value="">全部部件</option>{parts.map(p=><option key={p}>{p}</option>)}</select>}
    <p className="ec-view-note">{data.message}</p><div className="ec-human-actions"><button onClick={()=>onAsk("请保留当前结构和输入，只重新渲染当前对象。")}>重新渲染</button><button onClick={()=>onAsk("请检查当前模型的比例与输入照片是否一致，指出需要修改的主要部位。")}>检查比例</button></div>
  </div>;
}

function Scene({data,onAsk,canResume}){
  return <div className="ec-scene-section"><div className="ec-human-card ec-camera-summary"><CheckCircle2 size={17}/><span>{data?.camera_status==="completed"?"相机重建完成":"相机尚未完成"}</span>{data?.registered_views!=null&&<small>{data.registered_views} 个视图 · 尺度：{data.scale}</small>}</div>
    {data?.overview?<div className="ec-render-frame"><img src={link(data.overview)} alt="实际重建场景总览"/></div>:<Empty title="等待场景总览">这里展示实际重建的背景图像，不显示高斯颗粒。</Empty>}
    <p className="ec-view-note">{data?.message??"加入多视角素材后可重建背景。"}</p>
    <div className="ec-human-actions"><button onClick={()=>onAsk("请基于当前输入重建背景：先检查素材和相机条件，具备条件时执行背景重建并生成一张实际场景总览，不生成填补图冒充重建。")}>重建背景</button>{canResume&&<button onClick={()=>onAsk("请从当前有效训练检查点恢复背景重建，保持原输入和训练配置，完成后生成实际总览。")}>从检查点恢复</button>}</div>
  </div>;
}

function Validation({data,delivery,onAsk}){
  return <div className="ec-validation-section"><div className="ec-human-card"><CheckCircle2 size={20}/><h2>{data?.status??"尚未执行物理验证"}</h2><p>{data?.condition??"模型外观与物理功能分别验证。"}</p>{data?.stale&&<p className="ec-human-warning">这是修改前的验证结果，当前版本需要重新验证。</p>}</div>
    {data?.media?.length>0&&<div className="ec-validation-media">{data.media.map(v=>v.type==="video"?<video key={v.id} src={link(v)} controls/>:<img key={v.id} src={link(v)} alt="实际验证记录"/>)}</div>}
    {data?.cases?.map((v,i)=><div key={i} className="ec-human-card"><strong>{v.name} · {v.status}</strong><p>{v.tests.length} 项协议检查，具体正常／失效对照见报告。</p></div>)}
    <div className="ec-human-actions"><button onClick={()=>onAsk("请检查当前资产和验证环境，使用已有受支持协议执行物理验证；保留正常与失效对照，说明实验条件。不支持的要求保持未验证。")}>执行验证</button><button onClick={()=>onAsk("请检查并打包当前版本，保留实际验证状态与未解决问题，不将未验证资产标成通过。")}>准备交付</button>{data?.artifact&&<a href={link(data.artifact)} target="_blank" rel="noreferrer">查看验证报告</a>}</div>
    {delivery&&<div className="ec-human-card"><strong>{delivery.stale?"历史交付，当前版本需更新":"交付文件"}</strong><div className="ec-delivery-files">{delivery.files.map(v=><a key={v.id} href={link(v)} download>{v.name}<Download size={12}/></a>)}</div></div>}
  </div>;
}

function WorkflowView({data,tab,onAsk,busy}){
  const names={"source-video.jpg":"原视频时间预览","object-evidence.jpg":"当前对象与原始画面","candidate.jpg":"生成模型 · 仅用于诊断","background-view.jpg":"3DGS 实际相机视图"};
  const kinds={input:["source-video.jpg","object-evidence.jpg"],model:["candidate.jpg"],scene:["background-view.jpg"]};
  const allowed=data.allowed_actions??[];
  const ask=text=>onAsk(`请检查当前工作流 ${data.workflow_id}，当前决策版本 ${data.revision}。${text} 保留原任务和已完成步骤；先确认最新状态。`);
  return <section className="ec-workflow-review">
    <div className="ec-human-actions">
      {allowed.some(a=>["sample_video","select_target","accept_structure"].includes(a))&&<button disabled={busy} onClick={()=>ask("根据已附原始画面、证据和未决项，完成当前需要的判断并继续固定流程。")}>确认方向并继续</button>}
      {allowed.includes("revise_structure")&&<button disabled={busy} onClick={()=>ask("结合当前审核指出的具体问题重新检查结构假设；不要为了编译而补造未知量。")}>重新检查结构</button>}
      {allowed.includes("repair_constraints")&&<button disabled={busy} onClick={()=>ask("从具体审核问题中选择需要修正的几何约束 ID，使用 repair_constraints 局部修订；保留其他关系、零件、配合与未知参数，不能用改写全文或放宽要求绕过审核。")}>局部修正关系</button>}
      {allowed.includes("propose_build")&&<button disabled={busy} onClick={()=>ask("在不改写原始未知量的前提下，提出独立的候选构建假设。逐项说明试验尺度、关节参考约定与行程等假设的理由，不将它们当作视频测量；保留原有几何审核。")}>尝试带假设的候选</button>}
      {allowed.includes("revisit_video")&&<button disabled={busy} onClick={()=>ask("根据失败诊断寻找原视频中的新证据，说明需要什么画面，再选择采样时间段。新旧对象身份需要重新确认。")}>回看视频补充证据</button>}
      {allowed.includes("stop")&&<button disabled={busy} onClick={()=>ask("停止这次工作流，保留未决项、候选和审核记录。")}>保留结果并停止</button>}
      {!allowed.length&&data.phase!=="stopped"&&data.phase!=="completed"&&<button disabled={busy} onClick={()=>ask("从已保存阶段恢复流程。")}>恢复流程</button>}
    </div>
    {(data.previews??[]).filter(p=>(kinds[tab]??[]).includes(p.name)).map(p=><figure className="ec-render-frame" key={p.name}><img src={link(p.artifact)} alt={names[p.name]??"实际结果"}/><figcaption>{names[p.name]}</figcaption></figure>)}
    {tab==="input"&&<p className="ec-view-note">原视频预览供选取时间段，不表示已经恢复相机视角或真实尺寸。</p>}
    {tab==="structure"&&(data.blueprint?<div className="ec-human-card"><h3>当前结构</h3><p>{(data.blueprint.part_instances??[]).map(p=>p.name).join("、")}</p><p>{(data.blueprint.joints??[]).length} 个关节 · {(data.blueprint.mates??[]).length} 个配合关系</p>{(data.blueprint.unresolved??[]).map((text,i)=><p key={i}>{text}</p>)}<small>结构描述通过，不等于接口几何或物理功能通过。</small></div>:<p>选定对象后会生成结构方案。</p>)}
    {tab==="structure"&&data.construction_readiness&&<div className="ec-human-card"><h3>建模前检查</h3><p>{data.construction_readiness.summary}</p>{data.construction_readiness.issues.slice(0,4).map((item,i)=><p key={i}>{item.message}</p>)}{data.construction_readiness.issues.length>4&&<small>还有 {data.construction_readiness.issues.length-4} 项问题，可结合检查记录重新检查结构。</small>}</div>}
    {tab==="structure"&&data.construction_hypothesis&&<div className="ec-human-card"><h3>候选构建假设</h3><p>以下数值用于试验，原始描述中的未知量仍保留。</p>{data.construction_hypothesis.plan.parameters.slice(0,8).map((p,i)=>{
      const parts=p.path.split("/"),last=parts.at(-1);
      const name=last==="rest"?"关节参考约定":last==="travel"?"候选行程":last==="mass_kg"?"候选质量":`候选尺寸 ${last.toUpperCase()}`;
      const value=({at_min:"参考位置为下限",at_max:"参考位置为上限",centered:"参考位置为区间中点"})[p.value]??p.value;
      return <p key={i}><strong>{name}：{value} {p.unit==="convention"?"":p.unit}</strong><br/>{p.rationale}</p>;
    })}<small>尺寸检查目前限于盒状配方包络；接触与物理功能须另行验证。</small></div>}
    {tab==="validation"&&<><p className="ec-view-note">物理验证：{({not_run:"尚未执行",physics_passed:"所选协议通过",failed:"未通过"})[data.physics_status]??data.physics_status}。模型外观、机械配合与物理功能分别记录。</p>{(data.critic??[]).filter(c=>c.issues?.length).map((c,i)=><div className="ec-human-card" key={i}><strong>本轮检查发现的问题</strong>{c.issues.map((issue,j)=><p key={j}>{typeof issue==="string"?issue:issue.message??issue.description??"存在未解决的几何或约束问题，请结合实际检查记录处理。"}</p>)}<small>失败候选用于诊断，不作为原视频证据。</small></div>)}</>}
  </section>;
}

export function HumanWorkspace({ctx,sessionId,task,runId,onRun,onUpload,onTechnical}){
  const [view,setView]=useState(null),[tab,setTab]=useState("input"),[selected,setSelected]=useState([]),[frameId,setFrameId]=useState(""),[objectId,setObjectId]=useState(""),[error,setError]=useState(""),[busy,setBusy]=useState(false),[text,setText]=useState(""),[comparison,setComparison]=useState(null),[compareId,setCompareId]=useState("");
  const input=useRef(null),current=useRef(0);
  useEffect(()=>{setView(null);setSelected([]);setFrameId("");setObjectId("");setError("");setComparison(null);setCompareId("");},[task?.id]);
  useEffect(()=>{
    if(!task?.id)return;
    let dead=false,busy=false;const controller=new AbortController(),generation=++current.current;
    setView(null);
    async function load(){if(busy)return;busy=true;try{
      const params=new URLSearchParams();if(runId)params.set("run_id",runId);if(objectId)params.set("object_id",objectId);
      const result=await request(`/tasks/${task.id}/workspace?${params}`,undefined,controller.signal);
      if(!dead&&generation===current.current)setView(result);
    }catch(e){if(!dead)setError(e.message);}finally{busy=false;}}
    load();const timer=setInterval(load,2500);
    let stream;
    if(runId&&typeof EventSource!=="undefined"){
      stream=new EventSource(`${base}/runs/${runId}/events`);stream.onmessage=()=>load();stream.addEventListener("status",load);
    }
    return()=>{dead=true;controller.abort();clearInterval(timer);stream?.close();};
  },[task?.id,runId,objectId]);
  useEffect(()=>{if(!sessionId||!task?.id)return;request("/selection",{session_id:sessionId,run_id:runId||null,object_id:objectId||null}).catch(e=>setError(e.message));},[sessionId,task?.id,runId,objectId]);
  useEffect(()=>{
    if(!compareId||!task?.id){setComparison(null);return;}
    const c=new AbortController();request(`/tasks/${task.id}/workspace?run_id=${encodeURIComponent(compareId)}`,undefined,c.signal).then(v=>setComparison(v.model?.renders?.[0]??null)).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();
  },[compareId,task?.id]);
  const frames=view?.perception?.frames??[],frame=frames.find(v=>v.id===frameId)??frames[0],objects=frame?.objects??[];
  const select=(id,multiple=false)=>setSelected(old=>multiple?(old.includes(id)?old.filter(v=>v!==id):[...old,id]):[id]);
  async function ask(message){
    if(!sessionId)return;setBusy(true);setError("");
    try{
      await request("/selection",{session_id:sessionId,run_id:runId||null,object_id:objectId||null});
      const r=await ctx.connection.rpc.call("/api","session/prompt",{args:{request:{requestId:crypto.randomUUID(),sessionId,mode:"queue",content:[{type:"text",text:message}],clientTimeZone:"Asia/Shanghai"}}});
      if(!r.ok||!r.value.accepted)throw Error(r.error?.message??"对话请求未被接收");setText("");
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function correct(kind){
    if(!view?.sources?.perception||!selected.length)return;setBusy(true);setError("");
    try{
      const r=await request("/human/action",{session_id:sessionId,operation:"perception_edit",sources:{perception:view.sources.perception},
        params:{frame_id:frame?.id,[kind]:selected},task:kind==="merge"?"按用户选择合并重复区域，保留主体身份":"按用户选择排除当前区域"});
      onRun(r.id,false);setSelected([]);
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function scope(target){
    if(!view?.sources?.evidence)return;setBusy(true);
    try{const r=await request("/human/action",{session_id:sessionId,operation:"scope_apply",sources:{evidence:view.sources.evidence},params:{objects:view.objects.map(o=>({asset_id:o.id,action:o.id===target?"build":"exclude",reason:o.id===target?"用户指定为重建主体":"用户本次只选择一个重建主体"}))},task:"按用户选择仅构建指定对象，其余对象不进入本轮模型"});onRun(r.id,false);}catch(e){setError(e.message);}finally{setBusy(false);}
  }
  const upload=async files=>{setBusy(true);try{await onUpload(files);}catch(e){setError(e.message);}finally{setBusy(false);if(input.current)input.current.value="";}};
  return <div className="ec-human-workspace">
    <div className="ec-human-title"><div><small>ENVCREATOR</small><h2>{task?.title??"新的重建"}</h2></div><button title="添加图片或视频" onClick={()=>input.current?.click()}><Upload size={15}/>添加素材</button><input ref={input} hidden type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/webm,video/quicktime,.mkv" multiple onChange={e=>upload([...e.target.files])}/></div>
    <div className="ec-human-progress"><span className={view?.status==="running"?"ec-pulse":""}/><span>{view?.message??"加入素材，描述你希望还原的对象。"}</span>{["running","queued","stopping"].includes(view?.status)&&<button title="停止当前作业" onClick={()=>request(`/runs/${view.run_id}/cancel`,{}).catch(e=>setError(e.message))}><Square size={12}/>停止</button>}<button className="ec-technical-link" onClick={onTechnical}>技术详情</button></div>
    {error&&<div role="alert" className="ec-notice ec-error"><AlertTriangle size={14}/>{error}<button onClick={()=>setError("")}>关闭</button></div>}
    <nav className="ec-human-nav">{[["input","输入与分割",ImageIcon],["structure","结构",GitBranch],["model","模型",Box],["scene","场景",Layers3],["validation","验证",CheckCircle2]].map(([id,title,Icon])=><button key={id} className={tab===id?"active":""} onClick={()=>setTab(id)}><Icon size={14}/>{title}</button>)}</nav>
    {view?.nodes?.length>0&&<details className="ec-process-map"><summary>重建链路 · {view.nodes.length} 步</summary><div>{view.nodes.map(node=><button key={node.id} onClick={()=>onRun(node.id,true)} title={node.origin}><span className={node.status === "running" ? "ec-pulse" : ""}/><strong>{node.title}</strong><small>{STATUS[node.status]??node.status}</small></button>)}</div></details>}
    <div className="ec-human-body"><aside className="ec-object-rail"><small>重建对象</small>{(view?.objects??[]).map(obj=><button key={obj.id} className={objectId===obj.id?"selected":""} onClick={()=>setObjectId(obj.id)}>{obj.image&&<img src={link(obj.image)} alt="对象参考"/>}<span>{obj.name}</span></button>)}{!view?.objects?.length&&<p>识别后显示对象</p>}
      <small>运行版本</small><select aria-label="工作区版本" value={runId??""} onChange={e=>onRun(e.target.value,!!e.target.value)}><option value="">当前结果</option>{task?.runs?.map((r,i)=><option key={r.id} value={r.id}>{task.runs.length-i} · {STATUS[r.status]??r.status}</option>)}</select>
      {tab==="model"&&<><small>对比版本</small><select aria-label="对比模型版本" value={compareId} onChange={e=>setCompareId(e.target.value)}><option value="">不对比</option>{task?.runs?.filter(r=>r.id!==runId).map((r,i)=><option key={r.id} value={r.id}>历史版本 {i+1}</option>)}</select></>}
    </aside><main className="ec-human-main">
      {!!view?.stale?.length&&tab!=="input"&&<div className="ec-notice">部分结果基于修改前的输入；可查看历史，新版本需要重新构建或验证。</div>}
      {(view?.decisions??[]).map((d,i)=><div className="ec-human-card ec-decision" key={i}><strong>{d.title}</strong><p>{d.message}</p>{d.kind==="scope"&&<div className="ec-human-actions">{view.objects.map(o=><button key={o.id} disabled={busy} onClick={()=>scope(o.id)}>只重建{o.name}</button>)}</div>}</div>)}
      {view?.workflow&&<WorkflowView data={view.workflow} tab={tab} onAsk={ask} busy={busy}/>}
      {!view?.workflow&&tab==="input"&&(frame?.image?<><div className="ec-human-actions">{frames.length>1&&<select aria-label="选择视频帧" value={frame.id} onChange={e=>{setFrameId(e.target.value);setSelected([]);}}>{frames.map((v,i)=><option key={v.id} value={v.id}>{v.time!=null?`${Number(v.time).toFixed(1)} 秒`:`视图 ${i+1}`}</option>)}</select>}<button disabled={busy||selected.length<2||!view.sources.perception} onClick={()=>correct("merge")}>合并所选区域</button><button disabled={busy||!selected.length||!view.sources.perception} onClick={()=>correct("exclude")}>排除所选区域</button></div>
        <Masks frame={frame} selected={selected} onSelect={select}/><div className="ec-object-cards">{objects.map((obj,i)=><button key={obj.id} className={selected.includes(obj.id)?"selected":""} onClick={e=>select(obj.id,e.shiftKey)}><i style={{background:COLORS[i%COLORS.length]}}/><span><strong>{obj.name}</strong><small>{[obj.role,obj.route,obj.origin].filter(Boolean).join(" · ")}</small>{selected.includes(obj.id)&&<p>{obj.explanation}</p>}</span></button>)}</div>
        <div className="ec-human-actions"><button disabled={busy} onClick={()=>ask("请依据当前分割与语义继续重建。如果存在重复或歧义，先展示问题和适合我的选择。")}>继续重建<ArrowRight size={14}/></button><button disabled={busy} onClick={()=>ask("请检查当前分割是否存在明显遗漏或重复。保持输入不变；有证据支持的修正可执行，否则用简短中文说明需要我补充什么。")}>调整分割</button></div>
      </>:<Empty title="从一个物体开始" action={<button className="ec-primary" disabled={busy||!task?.inputs?.length} onClick={()=>ask("请对已上传素材开始固定重建流程；视频先查看原始画面并判断采样方案，再生成对象证据。暂不构建，先展示对象和简短语义。")}>{busy?"正在提交…":"识别已上传素材"}<Play size={14}/></button>}>添加图片或视频，在对话中描述你希望还原的内容。{task?.inputs?.length>0&&<span>已加入 {task.inputs.length} 份素材。</span>}</Empty>)}
      {!view?.workflow&&tab==="structure"&&<Structure data={view?.structure} onAsk={ask}/>}
      {!view?.workflow&&tab==="model"&&<Asset data={view?.model} onAsk={ask} reference={comparison}/>}
      {!view?.workflow&&tab==="scene"&&<Scene data={view?.background} onAsk={ask} canResume={!!view?.sources?.checkpoint}/>}
      {!view?.workflow&&tab==="validation"&&<Validation data={view?.validation} delivery={view?.delivery} onAsk={ask}/>}
    </main></div>
    <form className="ec-human-compose" onSubmit={e=>{e.preventDefault();if(text.trim())ask(text.trim());}}><input aria-label="描述修改要求" placeholder="告诉生成器：哪里需要调整，或下一步做什么…" value={text} onChange={e=>setText(e.target.value)} maxLength={4000}/><button className="ec-primary" type="submit" disabled={busy||!text.trim()}><Send size={15}/><span>发送</span></button></form>
  </div>;
}
