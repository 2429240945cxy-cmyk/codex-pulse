#!/usr/bin/env python3
"""Incremental, metadata-only Codex metrics collector. Python standard library only."""
import argparse, datetime as dt, hashlib, json, logging, os, re, signal, sqlite3, time
import urllib.request, urllib.error
from pathlib import Path

VERSION = '1.0.0'
KEEP_DAYS = 90


def stamp(value):
    if not value: return None
    if isinstance(value, (int, float)): return float(value)
    try: return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError): return None


def quota_value(raw, observed):
    if not raw: return None
    result = {'observed_at': int(observed * 1000), 'windows': [], 'plan': raw.get('plan_type')}
    for key in ('primary', 'secondary'):
        w = raw.get(key)
        if not w: continue
        used = w.get('used_percent', w.get('usedPercent'))
        if used is None: continue
        mins = w.get('window_minutes', w.get('windowDurationMins'))
        result['windows'].append({'used_percent': float(used), 'remaining_percent': max(0, min(100, 100-float(used))),
            'window_minutes': mins, 'resets_at': w.get('resets_at', w.get('resetsAt'))})
    return result if result['windows'] else None


class Collector:
    def __init__(self, state_path, codex_home=None):
        self.home = Path(codex_home or Path.home()/'.codex')
        self.db = sqlite3.connect(state_path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, offset INTEGER, state TEXT);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, data TEXT, dirty INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS starts(ts REAL, thread TEXT, model TEXT, effort TEXT, transport TEXT);
        CREATE INDEX IF NOT EXISTS starts_thread_ts ON starts(thread, ts);
        CREATE TABLE IF NOT EXISTS items(ts REAL, thread TEXT, kind TEXT);
        CREATE INDEX IF NOT EXISTS items_thread_ts ON items(thread, ts);
        ''')
        self.scan_ms = 0

    def meta(self, key, default=None):
        r = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def setmeta(self, key, value):
        self.db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    def save(self, record):
        # This allowlisted record contains no chat contents, prompts, tool arguments or paths.
        encoded = json.dumps(record, separators=(',', ':'), ensure_ascii=False)
        self.db.execute('''INSERT INTO records VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET data=excluded.data,dirty=1
            WHERE records.data<>excluded.data''', (record['id'], encoded))

    def read_logs(self):
        path = self.home/'logs_2.sqlite'
        if not path.exists(): return
        try:
            c = sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True, timeout=1)
            last = self.meta('log_cursor', 0)
            maximum = c.execute('SELECT max(id) FROM logs').fetchone()[0] or 0
            if maximum < last: last = 0
            rows = c.execute('''SELECT id,ts,ts_nanos,thread_id,target,feedback_log_body FROM logs
                WHERE id>? AND ts>? AND target IN ('feedback_tags','codex_core::stream_events_utils')
                AND thread_id IS NOT NULL ORDER BY id''', (last, int(time.time()-KEEP_DAYS*86400)))
            for rid, sec, nano, thread, target, body in rows:
                body = body or ''; ts = sec+nano/1e9
                if target == 'feedback_tags' and ': endpoint="/responses"' in body:
                    model = re.search(r'model="([^\"]+)"', body)
                    effort = re.search(r'codex.turn.reasoning_effort=(\w+)', body)
                    transport = 'WS' if 'responses_websocket' in body else 'HTTP'
                    self.db.execute('INSERT INTO starts VALUES(?,?,?,?,?)', (ts,thread,model.group(1) if model else '',effort.group(1) if effort else '',transport))
                elif target == 'codex_core::stream_events_utils' and 'Output item ' in body and 'from="output_item_done"' not in body:
                    kind = re.search(r'item_type="([^\"]+)"', body)
                    if kind: self.db.execute('INSERT INTO items VALUES(?,?,?)', (ts,thread,kind.group(1)))
            self.setmeta('log_cursor', maximum)
            c.close()
        except sqlite3.Error as e: logging.warning('Log database temporarily unavailable: %s', type(e).__name__)

    def request(self, s, usage, ts, response_id=None):
        end = s.get('last_output_at')
        startrow = None
        if end:
            startrow = self.db.execute('SELECT ts,model,effort,transport FROM starts WHERE thread=? AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1',
                (s['thread'], end+.005, max(s.get('turn_start',0)-1, s.get('previous_request_end',0)))).fetchone()
        start = startrow[0] if startrow else None
        duration = max(0, (end-start)*1000) if start is not None and end and end>=start else None
        first = None
        if start is not None and end:
            row = self.db.execute('SELECT ts,kind FROM items WHERE thread=? AND ts>=? AND ts<=? ORDER BY ts LIMIT 1', (s['thread'],start,end+.005)).fetchone()
            if row: first = {'ms':round((row[0]-start)*1000,3),'kind':row[1]}
        rid = 'req:'+response_id if response_id else 'req:'+hashlib.sha256((s['thread']+':'+str(ts)+':'+str(usage)).encode()).hexdigest()[:28]
        output = int(usage.get('output_tokens') or 0)
        rec = {'id':rid,'kind':'request','thread_id':s['thread'],'turn_id':s.get('turn_id',''), 'ts':round((start or ts)*1000),
          'model':s.get('model') or (startrow[1] if startrow else '') or 'unknown', 'effort':s.get('effort') or (startrow[2] if startrow else '') or 'unknown',
          'status':'completed','input_tokens':int(usage.get('input_tokens') or 0),'cached_tokens':int(usage.get('cached_input_tokens') or 0),
          'cache_write_tokens':int(usage.get('cache_write_input_tokens') or 0),'output_tokens':output,'reasoning_tokens':int(usage.get('reasoning_output_tokens') or 0),'duration_ms':round(duration,3) if duration is not None else None,
          'ttft_ms':None,'first_item_ms':first['ms'] if first else None,'first_item_kind':first['kind'] if first else None,
          'tps':round(output/(duration/1000),3) if duration and duration>0 else None,'timing_source':'request_to_output_item_done' if start else 'unavailable',
          'transport':startrow[3] if startrow else 'unknown','billing':'subscription' if s.get('provider')=='openai' else 'unknown',
          'cost':None,'request_count':1,'updated_at':round(ts*1000)}
        self.save(rec)
        for key in ('input_tokens','cached_tokens','output_tokens','reasoning_tokens'):
            s[key] = s.get(key,0)+rec[key]
        s['request_count'] = s.get('request_count',0)+1
        s['previous_request_end'] = end or ts
        s['model_ms'] = s.get('model_ms',0)+(duration or 0)
        s['last_usage_at'] = ts

    def turn_record(self, s, updated):
        if not s.get('turn_id') or not s.get('turn_start'): return
        duration = s.get('duration_ms')
        output = s.get('output_tokens',0)
        self.save({'id':'turn:'+s['thread']+':'+s['turn_id'],'kind':'turn','thread_id':s['thread'],'turn_id':s['turn_id'],
          'ts':round(s['turn_start']*1000),'model':s.get('model') or 'unknown','effort':s.get('effort') or 'unknown','status':s.get('status','running'),
          **{k:s.get(k,0) for k in ('input_tokens','cached_tokens','output_tokens','reasoning_tokens','request_count')},
          'duration_ms':duration,'ttft_ms':s.get('ttft_ms'),'first_item_ms':None,'tps':round(output/(duration/1000),3) if duration and duration>0 else None,
          'model_ms':round(s.get('model_ms',0),3),'timing_source':'native_turn_metrics','transport':'mixed','billing':'subscription' if s.get('provider')=='openai' else 'unknown',
          'cost':None,'updated_at':round(updated*1000)})

    def process(self, s, d):
        typ = d.get('type'); q = d.get('payload') or {}; ts = stamp(d.get('timestamp')) or time.time()
        if typ == 'session_meta':
            s['thread'] = q.get('id') or s.get('thread','unknown'); s['provider'] = q.get('model_provider')
        elif typ == 'turn_context':
            s.update(model=q.get('model') or s.get('model'), effort=q.get('effort') or q.get('reasoning_effort') or s.get('effort'))
            if q.get('turn_id'): s['turn_id'] = q['turn_id']
        elif typ == 'response_item':
            if q.get('type') in ('custom_tool_call','function_call','reasoning') or (q.get('type')=='message' and q.get('role')=='assistant'):
                s['last_output_at'] = ts
        elif typ == 'token_usage_record':
            if not s.get('turn_id'): s['turn_id'] = q.get('turn_id') or str(ts)
            self.request(s,q.get('usage') or {},ts,q.get('response_id'))
            s['skip_token_count'] = True
        elif typ == 'event_msg':
            event = q.get('type')
            if event == 'task_started':
                if s.get('turn_id') and s.get('status')=='running':
                    s['status']='interrupted'; self.turn_record(s,ts)
                base={k:s[k] for k in ('thread','model','effort','provider') if k in s}
                s.clear(); s.update(base,turn_id=q.get('turn_id') or str(ts),turn_start=ts,status='running')
            elif event == 'token_count':
                quota=quota_value(q.get('rate_limits'),ts)
                if quota and quota['observed_at']>self.meta('quota',{}).get('observed_at',0): self.setmeta('quota',quota)
                info=q.get('info') or {}
                signature=info.get('total_token_usage')
                if not s.pop('skip_token_count',False) and signature!=s.get('last_signature') and info.get('last_token_usage'):
                    self.request(s,info['last_token_usage'],ts)
                s['last_signature']=signature
            elif event == 'task_complete':
                s.update(status='completed',duration_ms=q.get('duration_ms'),ttft_ms=q.get('time_to_first_token_ms'))
                if s.get('duration_ms') is None: s['duration_ms']=max(0,round((ts-s.get('turn_start',ts))*1000))
                self.turn_record(s,ts)
            elif event in ('turn_aborted','task_cancelled'):
                s.update(status='interrupted',duration_ms=max(0,round((ts-s.get('turn_start',ts))*1000)))
                self.turn_record(s,ts)
        return ts

    def scan(self):
        started=time.monotonic(); self.read_logs(); paths=set()
        state_path=self.home/'state_5.sqlite'
        if state_path.exists():
            try:
                c=sqlite3.connect('file:'+str(state_path)+'?mode=ro',uri=True,timeout=1)
                paths.update(Path(r[0]) for r in c.execute('SELECT rollout_path FROM threads WHERE updated_at>?',(int(time.time()-KEEP_DAYS*86400),)))
                c.close()
            except sqlite3.Error: pass
        today=dt.datetime.now().strftime('%Y/%m/%d')
        paths.update((self.home/'sessions'/today).glob('*.jsonl'))
        for path in paths:
            if not path.is_file(): continue
            try:
                size=path.stat().st_size
                old=self.db.execute('SELECT offset,state FROM files WHERE path=?',(str(path),)).fetchone()
                offset,s=(old[0],json.loads(old[1])) if old and size>=old[0] else (0,{'thread':path.stem[-36:]})
                if offset==size: continue
                last=0
                with path.open('rb') as f:
                    f.seek(offset)
                    while True:
                        line=f.readline()
                        if not line or not line.endswith(b'\n'): break
                        offset=f.tell()
                        try: d=json.loads(line)
                        except (ValueError,UnicodeDecodeError): continue
                        last=self.process(s,d)
                if last: self.turn_record(s,last)
                self.db.execute('INSERT INTO files VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET offset=excluded.offset,state=excluded.state',(str(path),offset,json.dumps(s)))
            except (OSError,sqlite3.Error) as e: logging.warning('Skipping temporarily unavailable rollout: %s',type(e).__name__)
        self.db.commit(); self.scan_ms=round((time.monotonic()-started)*1000,1)

    def pending(self, limit=100):
        return [(r[0],r[1]) for r in self.db.execute('SELECT id,data FROM records WHERE dirty=1 LIMIT ?',(limit,))]

    def send(self, config, heartbeat=False):
        pending=self.pending()
        payload={'records':[json.loads(x[1]) for x in pending], 'quota':self.meta('quota'),
          'pricing':self.meta('pricing'),'collector':{'version':VERSION,'device':'Mac','scan_ms':self.scan_ms,'indexed_files':self.db.execute('SELECT count(*) FROM files').fetchone()[0],
          'indexed_records':self.db.execute('SELECT count(*) FROM records').fetchone()[0],'backlog':self.db.execute('SELECT count(*) FROM records WHERE dirty=1').fetchone()[0]}}
        req=urllib.request.Request(config['url'].rstrip('/')+'/api/ingest',data=json.dumps(payload,separators=(',',':')).encode(),method='POST',
          headers={'Content-Type':'application/json','X-Collector-Token':config['ingest_token'],**({'OAI-Sites-Authorization':'Bearer '+config['bypass_token']} if config.get('bypass_token') else {})})
        # Do not follow redirects carrying credentials to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        with urllib.request.build_opener(NoRedirect).open(req,timeout=20) as response:
            result=json.load(response)
            if result.get('accepted')!=len(pending): raise RuntimeError('Ingest acknowledgement mismatch')
        for rid,data in pending: self.db.execute('UPDATE records SET dirty=0 WHERE id=? AND data=?',(rid,data))
        self.db.commit()
        return len(pending)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--once',action='store_true');parser.add_argument('--index-only',action='store_true')
    args=parser.parse_args();confpath=Path(args.config);config=json.loads(confpath.read_text());state=confpath.parent/'metrics.sqlite'
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    collector=Collector(state,config.get('codex_home'));stopping=False
    from pricing import refresh
    last_price_attempt=0
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    last_heartbeat=0;delay=2
    while not stopping:
        try:
            collector.scan()
            now=time.time()
            prices=collector.meta('pricing')
            if not args.index_only and (not prices or now*1000-prices.get('updated_at',0)>86400000) and now-last_price_attempt>3600:
                last_price_attempt=now
                try: collector.setmeta('pricing',refresh(prices));collector.db.commit()
                except Exception as e:
                    if prices:
                        prices['last_error']=type(e).__name__;collector.setmeta('pricing',prices);collector.db.commit()
                    logging.warning('Official pricing refresh deferred (%s)',type(e).__name__)
            if args.index_only:
                print(json.dumps({'indexed_records':collector.db.execute('SELECT count(*) FROM records').fetchone()[0],'scan_ms':collector.scan_ms}));return
            while collector.pending() and not stopping:
                collector.send(config);last_heartbeat=time.monotonic()
            if time.monotonic()-last_heartbeat>15:
                collector.send(config,heartbeat=True);last_heartbeat=time.monotonic()
            delay=2
        except Exception as e:
            # Avoid logging network bodies, local file paths or credential-bearing request objects.
            logging.warning('Sync deferred (%s%s); retry in %ss',type(e).__name__,(' '+str(e.code)) if isinstance(e,urllib.error.HTTPError) else '',delay);delay=min(60,max(4,delay*2))
        if args.once: return
        for _ in range(int(delay*5)):
            if stopping: break
            time.sleep(.2)

if __name__=='__main__': main()
