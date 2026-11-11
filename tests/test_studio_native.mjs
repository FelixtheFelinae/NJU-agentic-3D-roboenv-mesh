import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { apply, registerTools } from '../integrations/studio/host.mjs';

test('completed native component results are lossless JSON with explicit missing values', async () => {
  const root=mkdtempSync(join(tmpdir(),'studio-json-')),tools=new Map();
  writeFileSync(join(root,'token'),'test-token');
  const server=createServer(async(req,res)=>{
    for await(const _ of req) {};
    res.setHeader('content-type','application/json');
    res.end(JSON.stringify({id:'completed-run',status:'completed',agent_observation:{data:{}}}));
  });
  await new Promise(done=>server.listen(0,'127.0.0.1',done));
  try {
    registerTools({tools:{register:t=>tools.set(t.name,t)}}, {
      backendURL:'http://127.0.0.1:'+server.address().port+'/',tokenFile:join(root,'token'),maxWallSeconds:60,debugComponentTools:true,
    });
    assert.ok(tools.has('envcreator_geometry_audit'));
    assert.ok(tools.has('envcreator_blueprint_repair'));
    assert.match(tools.get('envcreator_blueprint_repair').description,/constraint_ids/);
    assert.match(tools.get('envcreator_geometry_audit').description,/finite overlap/);
    assert.match(tools.get('envcreator_geometry').description,/sources.geometric_verification/);
    const value=await tools.get('envcreator_reference_validate').execute({sources:{reference:'source'},params:{stage:'qa'}},
      {agent:{id:'session'},callId:'call',signal:new AbortController().signal});
    assert.deepEqual(value,JSON.parse(JSON.stringify(value)));
    assert.equal(value.stage_errors,null);
    assert.equal(value.component,null);
    assert.equal(value.artifacts.total,0);
    assert.equal(value.run_id,'completed-run');
  } finally {await new Promise(done=>server.close(done));rmSync(root,{recursive:true});}
});

// Protocol fixtures, not evidence of live DSH/model execution.
test('native generate preserves call identity and propagates cancellation; browser routes keep the DSH auth fence', async () => {
  const root=mkdtempSync(join(tmpdir(),'studio-host-')),calls=[],tools=new Map();
  writeFileSync(join(root,'token'),'test-token');
  const backend=createServer(async(req,res)=>{let text='';for await(const chunk of req)text+=chunk;const body=text?JSON.parse(text):null;calls.push({path:req.url,body,authorization:req.headers.authorization});res.setHeader('content-type','application/json');res.end(JSON.stringify(req.url==='/api/native/invoke'?{id:'run-1',status:'running'}:{status:'stopping',accepted:true}));});
  await new Promise(done=>backend.listen(0,'127.0.0.1',done));
  let route,dispose,section;
  const ctx={tools:{register:t=>tools.set(t.name,t)},systemPrompt:{section:s=>section=s},
    connection:{requestRejection:req=>req.authorized?undefined:401},
    webServer:{register:r=>{route=r;return()=>{};}},effect:fn=>{dispose=fn();}};
  try {
    apply(ctx,{backendURL:'http://127.0.0.1:'+backend.address().port+'/',tokenFile:join(root,'token'),maxWallSeconds:60,debugComponentTools:true});
    assert.equal(typeof section.text(),'string');
    const controller=new AbortController();
    const execution=tools.get('envcreator_generate').execute({task:'create fixture',input_ids:['input-1']},{agent:{id:'native-session'},callId:'native-call',signal:controller.signal});
    const deadline=Date.now()+2000;
    while(!calls.some(v=>v.path==='/api/native/invoke')&&Date.now()<deadline)await new Promise(r=>setTimeout(r,10));
    controller.abort();
    await assert.rejects(execution);
    assert.ok(calls.some(v=>v.path==='/api/native/cancel'&&v.body.call_id==='native-call'&&v.body.session_id==='native-session'));
    const invocation=calls.find(v=>v.path==='/api/native/invoke');
    assert.equal(invocation.body.session_id,'native-session');
    assert.equal(invocation.authorization,'Bearer test-token');
    const response={status:0,writeHead(code){this.status=code;},end(){},setHeader(){}};
    await route.handler({authorized:false},response);
    assert.equal(response.status,401);
    await route.handler({authorized:true,url:'/envcreator/api/native/invoke'},response);
    assert.equal(response.status,404);
    await route.handler({authorized:true,url:'/envcreator/api/%6eative/invoke'},response);
    assert.equal(response.status,404);
  } finally {dispose?.();await new Promise(done=>backend.close(done));rmSync(root,{recursive:true});}
});

test('workflow is the default tool surface and source/candidate images use native attachments', async () => {
  const root=mkdtempSync(join(tmpdir(),'studio-workflow-')),tools=new Map(),saved=[];
  writeFileSync(join(root,'token'),'test-token');
  const backend=createServer(async(req,res)=>{
    for await(const _ of req) {};
    if(req.url.includes('/artifacts/')) {res.setHeader('content-type','image/jpeg');res.end(Buffer.from([255,216,255,217]));return;}
    res.setHeader('content-type','application/json');
    res.end(JSON.stringify({id:'call-result',status:'completed',state:{status:'recovery'},
      agent_observation:{data:{workflow:{workflow_id:'original',phase:'recovery',revision:3,
        previews:['source-video.jpg','candidate.jpg'],allowed_actions:['revisit_video','stop']}}},
      artifacts:{items:['source-video.jpg','candidate.jpg'].map((name,i)=>({id:String(i),ref:'output/'+name,type:'image'}))}}));
  });
  await new Promise(done=>backend.listen(0,'127.0.0.1',done));
  try {
    registerTools({tools:{register:t=>tools.set(t.name,t)},attachments:{saveImage:async v=>{
      saved.push(v);return {id:'saved-'+saved.length};
    }}}, {backendURL:'http://127.0.0.1:'+backend.address().port+'/',tokenFile:join(root,'token'),maxWallSeconds:60});
    assert.deepEqual([...tools.keys()],['envcreator_context','envcreator_inspect','envcreator_workflow_start','envcreator_workflow_advance']);
    const tool=tools.get('envcreator_workflow_advance');
    const decisionSchema=tool.parameters.properties.params.properties;
    assert.ok(decisionSchema.action.enum.includes('propose_build'));
    assert.ok(decisionSchema.action.enum.includes('repair_constraints'));
    assert.equal(decisionSchema.constraint_ids.maxItems,4);
    assert.equal(decisionSchema.evidence_refs.maxItems,16);
    assert.ok(decisionSchema.hypothesis.required.includes('source_blueprint_sha256'));
    const value=await tool.execute({parent_id:'original',params:{action:'resume'}},
      {agent:{id:'native-session'},callId:'native-call',signal:new AbortController().signal});
    assert.equal(saved.length,2);
    const blocks=tool.output.render({},value);
    assert.equal(blocks.filter(b=>b.type==='image').length,2);
    assert.equal(JSON.parse(blocks[0].text).agent_observation.data.workflow.phase,'recovery');
    assert.equal(JSON.parse(blocks[0].text).preview_attachments,undefined);
    assert.ok(blocks.some(b=>b.text==='candidate.jpg'));
  } finally {await new Promise(done=>backend.close(done));rmSync(root,{recursive:true});}
});
