const SECURITY={'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Cache-Control':'no-store'};
const json=(data,status=200,headers={})=>new Response(JSON.stringify(data),{status,headers:{...SECURITY,'Content-Type':'application/json; charset=utf-8',...headers}});
const fields=['id','kind','thread_id','turn_id','ts','model','effort','status','input_tokens','cached_tokens','cache_write_tokens','output_tokens','reasoning_tokens','duration_ms','ttft_ms','first_item_ms','first_item_kind','tps','timing_source','transport','billing','cost','request_count','updated_at','model_ms'];
const numeric=['ts','input_tokens','cached_tokens','cache_write_tokens','output_tokens','reasoning_tokens','duration_ms','ttft_ms','first_item_ms','tps','request_count','updated_at','model_ms'];
export function cleanRecord(raw){
  if(!raw||!['request','turn'].includes(raw.kind)||typeof raw.id!=='string'||raw.id.length>200) throw Error('Invalid record');
  const r={};for(const k of fields)if(raw[k]!==undefined)r[k]=raw[k];
  for(const k of numeric){if(r[k]!=null&&(!Number.isFinite(r[k])||r[k]<0))throw Error('Invalid numeric field');}
  for(const k of fields.filter(k=>!numeric.includes(k)&&k!=='cost'))if(r[k]!=null&&typeof r[k]==='string'&&r[k].length>240)throw Error('Field too long');
  if(!r.ts||!r.updated_at||!['completed','running','interrupted'].includes(r.status))throw Error('Invalid status or time');
  r.model=String(r.model||'unknown');r.effort=String(r.effort||'unknown');r.turn_id=String(r.turn_id||'');
  for(const k of ['input_tokens','cached_tokens','cache_write_tokens','output_tokens','reasoning_tokens'])r[k]=Math.floor(r[k]||0);
  return r;
}
export function estimate(group,pricing,tier='standard'){
  let model=group.model;let rates=pricing?.models?.[model]?.[tier];
  if(!rates){const base=Object.keys(pricing?.models||{}).sort((a,b)=>b.length-a.length).find(k=>model.startsWith(k+'-20'));rates=pricing?.models?.[base]?.[tier];model=base;}
  if(!rates)return null;
  const cached=Math.min(group.cached_tokens||0,group.input_tokens||0),write=Math.min(group.cache_write_tokens||0,Math.max(0,(group.input_tokens||0)-cached));
  if((cached&&rates.cached==null)||(write&&rates.write==null))return null;
  const longer=group.long_context===1 || (group.input_tokens>rates.long_threshold&&!group.aggregate);
  const im=longer&&rates.long_context?2:1,om=longer&&rates.long_context?1.5:1;
  const usd=((Math.max(0,(group.input_tokens||0)-cached-write)*rates.input+cached*(rates.cached||0)+write*(rates.write||0))*im+(group.output_tokens||0)*rates.output*om)/1e6;
  const cr=pricing?.credits?.[model];
  const credits=cr?((Math.max(0,(group.input_tokens||0)-cached)*cr.input+cached*cr.cached+(group.output_tokens||0)*cr.output)/1e6):null;
  return {usd,credits,tier,long_context:!!longer};
}
function whereFor(p,kind){
 const days=Number(p.get('days')||1);const clauses=['kind=?'];const args=[kind];
 if([1,7,30,90].includes(days)){clauses.push('ts>=?');args.push(Date.now()-days*86400000);}
 for(const k of ['model','effort','status']){const value=p.get(k);if(value&&value!=='all'){clauses.push(k+'=?');args.push(value.slice(0,240));}}
 const q=p.get('q')?.slice(0,120);if(q){clauses.push('(id LIKE ? OR turn_id LIKE ?)');args.push('%'+q+'%','%'+q+'%');}
 return {sql:clauses.join(' AND '),args};
}
const aggregation=`count(*) as count,coalesce(sum(input_tokens),0) input_tokens,coalesce(sum(cached_tokens),0) cached_tokens,coalesce(sum(output_tokens),0) output_tokens,coalesce(sum(reasoning_tokens),0) reasoning_tokens,coalesce(sum(CASE WHEN duration_ms>0 THEN duration_ms ELSE 0 END),0) timed_ms,coalesce(sum(CASE WHEN duration_ms>0 THEN output_tokens ELSE 0 END),0) timed_output`;
const costGrouping=`model,CASE WHEN input_tokens>272000 THEN 1 ELSE 0 END long_context,count(*) count,sum(input_tokens) input_tokens,sum(cached_tokens) cached_tokens,sum(output_tokens) output_tokens,sum(coalesce(json_extract(payload,'$.cache_write_tokens'),0)) cache_write_tokens`;
async function dashboard(request,env){
 const u=new URL(request.url),p=u.searchParams;const kind=p.get('kind')==='request'?'request':'turn';const tier=['standard','fast','ultrafast'].includes(p.get('tier'))?p.get('tier'):'standard';
 const w=whereFor(p,kind),wr=whereFor(p,'request');const limit=Math.min(100,Math.max(10,Number(p.get('limit'))||40)),offset=Math.max(0,Math.min(100000,Number(p.get('offset'))||0));
 const latest=await env.DB.prepare('SELECT key,value FROM metadata').all();const meta=Object.fromEntries(latest.results.map(r=>[r.key,JSON.parse(r.value)]));
 const etag='"'+String(meta.last_sync||0)+'-'+Array.from(p.entries()).map(x=>x.join(':')).join('|')+'"';
 if(request.headers.get('If-None-Match')===etag)return new Response(null,{status:304,headers:{...SECURITY,ETag:etag}});
 const results=await env.DB.batch([
  env.DB.prepare('SELECT payload FROM records WHERE '+w.sql+' ORDER BY ts DESC LIMIT ? OFFSET ?').bind(...w.args,limit,offset),
  env.DB.prepare('SELECT '+aggregation+' FROM records WHERE '+wr.sql).bind(...wr.args),
  env.DB.prepare('SELECT '+aggregation+' FROM records WHERE kind=\'request\''),
  env.DB.prepare('SELECT count(*) n FROM records WHERE '+w.sql).bind(...w.args),
  env.DB.prepare('SELECT DISTINCT model FROM records ORDER BY model'),
  env.DB.prepare('SELECT ttft_ms,tps FROM records WHERE '+w.sql+' AND status=\'completed\' ORDER BY ts DESC LIMIT 1000').bind(...w.args),
  env.DB.prepare('SELECT '+costGrouping+' FROM records WHERE '+wr.sql+' GROUP BY model,long_context').bind(...wr.args),
  env.DB.prepare('SELECT '+costGrouping+' FROM records WHERE kind=\'request\' GROUP BY model,long_context'),
  env.DB.prepare('SELECT CAST(ts/3600000 AS INTEGER)*3600000 bucket,count(*) count,avg(ttft_ms) ttft_ms,avg(tps) tps FROM records WHERE '+w.sql+' AND ts>=? GROUP BY bucket ORDER BY bucket').bind(...w.args,Date.now()-Math.min(Number(p.get('days'))||1,7)*86400000)
 ]);
 const rows=results[0].results.map(r=>JSON.parse(r.payload));
 if(kind==='turn'&&rows.length){
  const ids=rows.map(r=>r.turn_id);const costs=await env.DB.prepare('SELECT turn_id,'+costGrouping+' FROM records WHERE kind=\'request\' AND turn_id IN ('+ids.map(()=>'?').join(',')+') GROUP BY turn_id,model,long_context').bind(...ids).all();
  const byTurn={};for(const g of costs.results){const e=estimate({...g,aggregate:true},meta.pricing,tier);const a=byTurn[g.turn_id]||={usd:0,credits:0,unpriced:0};if(e){a.usd+=e.usd;a.credits+=e.credits||0;}else a.unpriced+=g.count;}
  for(const r of rows)r.estimate=byTurn[r.turn_id]||null;
 }else for(const r of rows)r.estimate=estimate(r,meta.pricing,tier);
 function totalEstimate(groups){let usd=0,credits=0,unpriced=0;for(const g of groups){const e=estimate({...g,aggregate:true},meta.pricing,tier);if(e){usd+=e.usd;credits+=e.credits||0;}else unpriced+=g.count;}return {usd,credits,unpriced};}
 const samples=results[5].results;function percentile(key,quantile){const v=samples.map(s=>s[key]).filter(Number.isFinite).sort((a,b)=>a-b);return v.length?v[Math.floor((v.length-1)*quantile)]:null;}
 return json({rows,total:results[3].results[0].n,summary:{...results[1].results[0],estimate:totalEstimate(results[6].results)},lifetime:{...results[2].results[0],estimate:totalEstimate(results[7].results)},models:results[4].results.map(x=>x.model),
  latency:{p50:percentile('ttft_ms',.5),p95:percentile('ttft_ms',.95),sample_count:samples.filter(s=>s.ttft_ms!=null).length},chart:results[8].results,meta,kind,tier,offset,limit,server_time:Date.now()},200,{ETag:etag});
}
async function ingest(request,env){
 if(!env.INGEST_TOKEN||request.headers.get('X-Collector-Token')!==env.INGEST_TOKEN)return json({error:'Unauthorized collector'},401);
 const length=Number(request.headers.get('Content-Length')||0);if(length>2000000)return json({error:'Payload too large'},413);
 const body=await request.text();if(body.length>2000000)return json({error:'Payload too large'},413);
 const data=JSON.parse(body);if(!Array.isArray(data.records)||data.records.length>100)return json({error:'Invalid batch'},400);
 const records=data.records.map(cleanRecord),statements=[];
 for(const r of records)statements.push(env.DB.prepare(`INSERT INTO records(id,kind,turn_id,ts,model,effort,status,input_tokens,cached_tokens,output_tokens,reasoning_tokens,duration_ms,ttft_ms,tps,payload,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,turn_id=excluded.turn_id,ts=excluded.ts,model=excluded.model,effort=excluded.effort,status=excluded.status,input_tokens=excluded.input_tokens,cached_tokens=excluded.cached_tokens,output_tokens=excluded.output_tokens,reasoning_tokens=excluded.reasoning_tokens,duration_ms=excluded.duration_ms,ttft_ms=excluded.ttft_ms,tps=excluded.tps,payload=excluded.payload,updated_at=excluded.updated_at WHERE excluded.updated_at>=records.updated_at`).bind(r.id,r.kind,r.turn_id,r.ts,r.model,r.effort,r.status,r.input_tokens,r.cached_tokens,r.output_tokens,r.reasoning_tokens,r.duration_ms??null,r.ttft_ms??null,r.tps??null,JSON.stringify(r),r.updated_at));
 const now=Date.now();const entries={last_sync:now};
 if(data.collector)entries.collector={device:String(data.collector.device||'Mac').slice(0,60),version:String(data.collector.version||'').slice(0,20),scan_ms:Number(data.collector.scan_ms)||0,indexed_files:Number(data.collector.indexed_files)||0,indexed_records:Number(data.collector.indexed_records)||0,backlog:Number(data.collector.backlog)||0};
 if(data.quota?.windows)entries.quota=data.quota;
 if(data.pricing?.models&&data.pricing.source==='https://developers.openai.com/api/docs/pricing')entries.pricing=data.pricing;
 for(const [key,value]of Object.entries(entries))statements.push(env.DB.prepare('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').bind(key,JSON.stringify(value)));
 for(let i=0;i<statements.length;i+=50)await env.DB.batch(statements.slice(i,i+50));
 return json({accepted:records.length,synced_at:now});
}
export default {async fetch(request,env){
 const u=new URL(request.url);
 // Hosted reads require explicit authentication. Local preview stays loopback-only.
 if(u.pathname!=='/api/ingest' && !['localhost','127.0.0.1','[::1]'].includes(u.hostname) && env.ACCESS_MODE!=='sites'){
  if(!env.VIEWER_TOKEN)return json({error:'Set VIEWER_TOKEN before publishing this dashboard'},503);
  const auth=request.headers.get('Authorization')||'';
  let supplied='';try{if(auth.startsWith('Basic '))supplied=atob(auth.slice(6)).split(':').slice(1).join(':');}catch{}
  if(supplied!==env.VIEWER_TOKEN)return new Response('Authentication required',{status:401,headers:{...SECURITY,'WWW-Authenticate':'Basic realm="Codex Pulse", charset="UTF-8"'}});
 }
 try{
  if(u.pathname==='/api/ingest'&&request.method==='POST')return await ingest(request,env);
  if(request.method!=='GET')return json({error:'Method not allowed'},405);
  if(u.pathname==='/api/dashboard')return await dashboard(request,env);
  if(u.pathname==='/api/health'){await env.DB.prepare('SELECT 1').first();return json({ok:true,version:'1.0.0'});}
  if(u.pathname==='/')return new Response(HTML,{headers:{...SECURITY,'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://*.chatgpt.com https://chatgpt.com https://*.openai.com"}});
  return json({error:'Not found'},404);
 }catch(error){console.error('Request failed',u.pathname,error.name,error.message);return json({error:'服务暂时不可用，请稍后重试'},500);}
}};
