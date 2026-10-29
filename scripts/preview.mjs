import http from 'node:http';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import worker from '../worker/index.js';
const db=new DatabaseSync('preview.sqlite');
db.exec(readFileSync('drizzle/0000_metrics.sql','utf8').replaceAll('--> statement-breakpoint','').replaceAll('CREATE TABLE ','CREATE TABLE IF NOT EXISTS ').replaceAll('CREATE INDEX ','CREATE INDEX IF NOT EXISTS '));
const adapter={prepare(sql){return {args:[],bind(...args){this.args=args;return this;},async all(){return {results:db.prepare(sql).all(...this.args)};},async first(){return db.prepare(sql).get(...this.args)||null;},async run(){return db.prepare(sql).run(...this.args);},sql};},async batch(stmts){return stmts.map(s=>{const q=db.prepare(s.sql);return s.sql.trim().match(/^SELECT/i)?{results:q.all(...s.args)}:{success:true,meta:q.run(...s.args)};});}};
const env={DB:adapter,INGEST_TOKEN:'local-preview'};
http.createServer(async(req,res)=>{try{const buffers=[];for await(const c of req)buffers.push(c);const init={method:req.method,headers:req.headers};if(!['GET','HEAD'].includes(req.method))init.body=Buffer.concat(buffers);const r=await worker.fetch(new Request('http://127.0.0.1:8768'+req.url,init),env,{});res.writeHead(r.status,Object.fromEntries(r.headers));res.end(Buffer.from(await r.arrayBuffer()));}catch(e){res.writeHead(500);res.end(e.message);console.error(e);}}).listen(8768,'127.0.0.1',()=>console.log('Local: http://127.0.0.1:8768'));
