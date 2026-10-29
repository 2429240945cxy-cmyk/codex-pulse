import test from 'node:test';import assert from 'node:assert/strict';
import {estimate,cleanRecord} from '../worker/index.js';
const pricing={models:{'gpt-6.1-sol':{standard:{input:2,cached:.1,write:2.5,output:10,long_context:true,long_threshold:272000}}},credits:{'gpt-6.1-sol':{input:50,cached:2.5,output:250}}};
test('cached input is charged once and reasoning is not added twice',()=>{const e=estimate({model:'gpt-6.1-sol',input_tokens:100000,cached_tokens:90000,output_tokens:1000,reasoning_tokens:900},pricing);assert.ok(Math.abs(e.usd-.039)<1e-9);assert.ok(Math.abs(e.credits-.975)<1e-9);});
test('long context uses full request threshold, not accumulated history',()=>{const r={model:'gpt-6.1-sol',input_tokens:1000000,cached_tokens:900000,output_tokens:10000};assert.ok(Math.abs(estimate(r,pricing).usd-.73)<1e-9);assert.ok(Math.abs(estimate({...r,aggregate:true,long_context:0},pricing).usd-.39)<1e-9);});
test('unsupported model is explicitly unpriced',()=>{assert.equal(estimate({model:'private-model',input_tokens:30},pricing),null)});
test('collector ingestion strips prompts and secrets',()=>{const r=cleanRecord({id:'r',kind:'request',ts:1,updated_at:2,status:'completed',model:'m',input_tokens:3,prompt:'PRIVATE',api_key:'SECRET'});assert.equal(r.prompt,undefined);assert.equal(r.api_key,undefined);assert.equal(r.input_tokens,3);});
test('invalid measurements are rejected instead of polluting aggregates',()=>{assert.throws(()=>cleanRecord({id:'r',kind:'turn',ts:1,updated_at:2,status:'completed',input_tokens:-1}));});

test('remote dashboard fails closed without viewer configuration',async()=>{
 const {default:worker}=await import('../worker/index.js');
 const res=await worker.fetch(new Request('https://pulse.example/api/health'),{});
 assert.equal(res.status,503);
});
test('remote dashboard requires viewer password independently of upload token',async()=>{
 const {default:worker}=await import('../worker/index.js');
 const env={VIEWER_TOKEN:'test-viewer',DB:{prepare(){return{async first(){return 1;}}}}};
 assert.equal((await worker.fetch(new Request('https://pulse.example/api/health'),env)).status,401);
 const req=new Request('https://pulse.example/api/health',{headers:{Authorization:'Basic '+btoa('viewer:test-viewer')}});
 assert.equal((await worker.fetch(req,env)).status,200);
});
