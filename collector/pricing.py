"""Fetch official price tables daily, retain last valid table on network/schema errors."""
import json, re, time, urllib.request
from pathlib import Path
from html.parser import HTMLParser
API_URL='https://developers.openai.com/api/docs/pricing'
CREDIT_URL='https://learn.chatgpt.com/docs/pricing'

def unpack(v):
    if isinstance(v,list) and len(v)==2 and isinstance(v[0],int):
        return [unpack(x) for x in v[1]] if v[0]==1 else unpack(v[1])
    if isinstance(v,dict): return {k:unpack(x) for k,x in v.items()}
    return v

class Prices(HTMLParser):
    def __init__(self): super().__init__();self.catalog={};self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='astro-island' and a.get('component-export')=='TextTokenPricingTables':
            p=unpack(json.loads(a['props']));tier=p.get('tier')
            if tier not in ('standard','fast','ultrafast'): return
            for row in p.get('rows',[]):
                model=re.sub(r'\s*\(.*','',row[0]).lower()
                if len(row)==5: _,inp,cached,write,out=row
                elif len(row)==4: _,inp,cached,out=row;write=None
                else: continue
                if not isinstance(inp,(int,float)) or not isinstance(out,(int,float)): continue
                self.catalog.setdefault(model,{})[tier]={'input':inp,'cached':cached if isinstance(cached,(int,float)) else None,'write':write if isinstance(write,(int,float)) else None,'output':out,
                    'long_context':bool(model.startswith('gpt-6-') or model.startswith('gpt-6.1-') or model in ('gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna','gpt-5.5','gpt-5.5-pro','gpt-5.4','gpt-5.4-pro')),'long_threshold':272000}
        if tag=='tr':self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell=''
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:self.row.append(self.cell.strip());self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None

def fetch_page(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'CodexPulse/1.0 (+personal metrics; daily pricing refresh)'}),timeout=15) as r:
        return r.read(3000000).decode('utf-8')

def refresh(previous=None,api_html=None,credit_html=None):
    p=Prices();p.feed(api_html if api_html is not None else fetch_page(API_URL))
    if len(p.catalog)<5 or 'gpt-6.1-sol' not in p.catalog: raise ValueError('Official pricing format not recognized')
    credits={}
    c=Prices()
    credit_error=None
    try: c.feed(credit_html if credit_html is not None else fetch_page(CREDIT_URL))
    except Exception as e:
        credit_error=type(e).__name__
        seed=Path(__file__).with_name('credit-seed.json')
        if previous and previous.get('credits'): credits=previous['credits']
        elif seed.exists(): credits=json.loads(seed.read_text())['credits']
    for row in c.rows:
        if len(row)==4 and 'credits' in row[1] and row[0].startswith(('GPT-','Daybreak')):
            try: values=[float(re.sub(r'[^\d.]','',x)) for x in row[1:]]
            except ValueError:continue
            model=row[0].lower().replace(' ','-')
            credits[model]={'input':values[0],'cached':values[1],'output':values[2]}
    if not credits: raise ValueError('Official credit pricing format not recognized')
    return {'updated_at':int(time.time()*1000),'source':API_URL,'credit_source':CREDIT_URL,'currency':'USD','per_tokens':1000000,
            'models':p.catalog,'credits':credits,'automatic_refresh_hours':24,'basis':'current_official_rate_card','last_error':None,'credits_last_error':credit_error}
