"""Bali flight monitor: Python 3.11+, standard library only."""
import argparse, hashlib, hmac, json, math, os, secrets, sqlite3, subprocess, sys, time
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('FLIGHT_DB',str(ROOT/'data'/'flights.sqlite')))
TZ=ZoneInfo('Australia/Brisbane')
DEFAULT=dict(start='2026-10-14',end='2026-12-14',currency='USD',target=180,drop_percent=5,nonstop=True,carry_on=True,daily_hour=8,weekly_hour=9,paused=False)
def now(): return datetime.now(timezone.utc).isoformat()
def connect():
    DB.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=15); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,observed TEXT NOT NULL,source TEXT NOT NULL,coverage TEXT NOT NULL,count INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS fares(run_id TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(run_id,key));
    CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,created TEXT NOT NULL,message TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,created TEXT NOT NULL,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,updated TEXT NOT NULL);''')
    c.execute('INSERT OR IGNORE INTO settings VALUES(1,?)',(json.dumps(DEFAULT),)); c.commit(); return c
def settings(c): return json.loads(c.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
def validate_settings(v):
    if set(v)!=set(DEFAULT): raise ValueError('All settings fields required; unknown fields rejected.')
    if date.fromisoformat(v['start'])>date.fromisoformat(v['end']): raise ValueError('Invalid date window.')
    if v['currency'] not in ('USD','AUD'): raise ValueError('Choose USD or AUD; no implicit conversion.')
    for k in ('target','drop_percent'):
        if type(v[k]) not in (int,float) or not math.isfinite(v[k]) or v[k]<0: raise ValueError('Invalid threshold.')
    if v['drop_percent']>100: raise ValueError('Percentage exceeds 100.')
    for k in ('daily_hour','weekly_hour'):
        if type(v[k]) is not int or not 0<=v[k]<=23: raise ValueError('Hours must be 0–23.')
    for k in ('nonstop','carry_on','paused'):
        if type(v[k]) is not bool: raise ValueError('Filters must be boolean.')
    return v
def safe_url(v):
    p=urlparse(v or '')
    return v if p.scheme=='https' and (p.hostname=='trip.com' or (p.hostname or '').endswith('.trip.com')) else None
def normalize(p):
    if not isinstance(p,dict) or p.get('isError'): raise ValueError('Provider error; existing results retained.')
    q=p.get('structuredContent',p)
    if not isinstance(q,dict): raise ValueError('Structured content must be an object.')
    f=q.get('flight',q)
    if not isinstance(f,dict): raise ValueError('Flight result must be an object.')
    if f.get('success') is not True or not isinstance(f.get('flights'),list): raise ValueError('Successful structured Trip.com response required.')
    if f.get('tripType','oneWay')!='oneWay' or len(f['flights'])>1000: raise ValueError('Invalid one-way result batch.')
    out=[]
    for x in f['flights']:
        journals=x.get('journalList',[])
        if len(journals)!=1: raise ValueError('One complete journey required per offer.')
        j=journals[0]['journal']; seg=journals[0]['segments']
        if j['departCity']!='BNE' or j['arriveCity']!='DPS': raise ValueError('Only BNE → DPS is supported.')
        if j.get('cabinClass') not in ('Y','economy','Economy'): raise ValueError('Economy offers only.')
        dep=datetime.fromisoformat(j['departTime']); datetime.fromisoformat(j['arriveTime'])
        price=x['price']; currency=x.get('currency')
        if type(price) not in (int,float) or not math.isfinite(price) or price<=0: raise ValueError('Invalid price.')
        if currency not in ('USD','AUD','CNY','EUR','GBP','SGD','IDR'): raise ValueError('Missing or unsupported currency.')
        if not seg or type(j['duration']) is not int or j['duration']<=0: raise ValueError('Missing duration or segments.')
        bag=j.get('baggageAllowance',{}); carry=bag.get('hasCarryOn')
        if carry is not None and type(carry) is not bool: raise ValueError('Invalid carry-on allowance.')
        direct=j.get('isTransfer') is False and len(seg)==1 and seg[0].get('isStop') is False
        key=hashlib.sha256(json.dumps([j['departTime'],[s['flightNo'] for s in seg],currency,carry,j['cabinClass']]).encode()).hexdigest()[:24]
        out.append(dict(key=key,date=dep.date().isoformat(),depart=j['departTime'],arrive=j['arriveTime'],
            airline=' / '.join(dict.fromkeys(s['airlineName'] for s in seg)),flight=' / '.join(s['flightNo'] for s in seg),
            price=price,currency=currency,duration=j['duration'],nonstop=direct,carry_on=carry,checked=bag.get('hasCheckedBaggage'),
            url=safe_url(x.get('deeplinkUrls',{}).get('onlineUrl'))))
    return out,f
def eligible(f,s):
    return max(s['start'],datetime.now(TZ).date().isoformat())<=f['date']<=s['end'] and f['currency']==s['currency'] and (not s['nonstop'] or f['nonstop']) and (not s['carry_on'] or f['carry_on'] is True)
def latest(c):
    result=[]; seen=set()
    for r in c.execute('SELECT f.value,r.observed,r.source,r.id FROM fares f JOIN runs r ON f.run_id=r.id ORDER BY r.observed DESC,r.rowid DESC'):
        f=json.loads(r['value'])
        if f['key'] in seen: continue
        seen.add(f['key']); f.update(observed=r['observed'],source=r['source'],run_id=r['id']); result.append(f)
    return result
def ingest(p,source='Trip.com live tool'):
    fares,meta=normalize(p); observed=p.get('observed_at',now()); dt=datetime.fromisoformat(observed)
    if dt.tzinfo is None or dt>datetime.now(timezone.utc)+timedelta(minutes=5): raise ValueError('Timestamp needs timezone and must not be in future.')
    observed=dt.astimezone(timezone.utc).isoformat()
    rid=p.get('run_id') or meta.get('transactionId') or hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()
    if not isinstance(rid,str) or len(rid)>200: raise ValueError('Invalid run ID.')
    coverage=json.dumps(dict(date=meta.get('queryDepartDate'),query=meta.get('rawQuery',''),scope='Returned offers only; not exhaustive calendar coverage'))
    with connect() as c:
        if c.execute('SELECT 1 FROM runs WHERE id=?',(rid,)).fetchone(): return dict(imported=0,duplicate=True,alerts=[])
        s=settings(c); old={f['key']:f for f in latest(c) if f['source']!='Conversation baseline'}
        c.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid,observed,source,coverage,len(fares)))
        messages=[]
        for f in fares:
            c.execute('INSERT OR REPLACE INTO fares VALUES(?,?,?)',(rid,f['key'],json.dumps(f)))
            prev=old.get(f['key'])
            if source=='Conversation baseline' or not eligible(f,s) or (prev and observed<=prev['observed']): continue
            reasons=[]
            if f['price']<=s['target'] and (not prev or prev['price']>s['target']): reasons.append('target reached')
            if prev and f['price']<prev['price'] and (prev['price']-f['price'])/prev['price']*100>=s['drop_percent']:
                reasons.append(f"down {(prev['price']-f['price'])/prev['price']*100:.1f}% from {prev['price']:g}")
            best=min((v['price'] for v in old.values() if eligible(v,s) and v['observed']<observed),default=None)
            if best is not None and f['price']<best: reasons.append('new lowest observed qualifying fare')
            if reasons:
                msg=f"{f['date']} · {f['flight']} · {f['currency']} {f['price']:g}: "+'; '.join(reasons)
                aid=hashlib.sha256((f['key']+str(f['price'])).encode()).hexdigest()
                if c.execute('INSERT OR IGNORE INTO alerts VALUES(?,?,?)',(aid,observed,msg)).rowcount: messages.append(msg)
        return dict(imported=len(fares),duplicate=False,alerts=messages)
def state():
    with connect() as c:
        s=settings(c); fares=sorted((f for f in latest(c) if eligible(f,s)),key=lambda f:(not f['nonstop'],f['price'],f['duration']))
        for f in fares: f['stale']=(datetime.now(timezone.utc)-datetime.fromisoformat(f['observed'])).total_seconds()>86400
        runs=[dict(r) for r in c.execute('SELECT * FROM runs ORDER BY observed DESC LIMIT 40')]; history=[]
        for r in reversed(runs):
            rows=[json.loads(v[0]) for v in c.execute('SELECT value FROM fares WHERE run_id=?',(r['id'],))]
            prices=[f['price'] for f in rows if eligible(f,s)]
            if prices: history.append(dict(observed=r['observed'],price=min(prices),source=r['source']))
        return dict(settings=s,fares=fares,history=history,runs=runs,
            alerts=[dict(r) for r in c.execute('SELECT * FROM alerts ORDER BY created DESC LIMIT 30')],
            reports=[dict(r) for r in c.execute('SELECT * FROM reports ORDER BY created DESC LIMIT 10')],
            jobs=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY updated DESC LIMIT 10')],
            provider_configured=bool(os.environ.get('FLIGHT_PROVIDER_COMMAND')),
            expired=datetime.now(TZ).date().isoformat()>s['end'])
def report():
    st=state(); fares=st['fares'][:5]
    text='Brisbane → Bali · weekly shortlist\n'+'\n'.join(f"{f['date']} {f['flight']} — {f['currency']} {f['price']:g}, {f['duration']//60}h {f['duration']%60:02}m"+(' · stale' if f['stale'] else '') for f in fares)
    if not fares: text+='\nNo qualifying observations. Check provider status and coverage.'
    with connect() as c:
        for f in fares:
            row=c.execute('SELECT value FROM fares JOIN runs ON fares.run_id=runs.id WHERE fares.key=? AND runs.observed<? AND runs.source!=? ORDER BY runs.observed DESC LIMIT 1',(f['key'],f['observed'],'Conversation baseline')).fetchone()
            if row:
                previous=json.loads(row[0]); delta=f['price']-previous['price']
                text+=f"\n{f['flight']} {f['date']}: {delta:+g} {f['currency']} versus previous comparable observation."
    text+='\nOnly observed offers are ranked. Reconfirm price and baggage before booking.'
    rid=datetime.now(TZ).date().isoformat()
    with connect() as c: c.execute('INSERT OR REPLACE INTO reports VALUES(?,?,?)',(rid,now(),text))
    return dict(id=rid,text=text)
def plan():
    s=state()['settings']; start=max(date.fromisoformat(s['start']),datetime.now(TZ).date()); end=date.fromisoformat(s['end']); windows=[]
    while start<=end:
        next_month=(start.replace(day=28)+timedelta(days=4)).replace(day=1); last=min(end,next_month-timedelta(days=1))
        windows.append(dict(start=start.isoformat(),end=last.isoformat())); start=last+timedelta(days=1)
    return dict(route='BNE-DPS',passengers=dict(adults=1,children=0,infants=0),cabin='economy',trip_type='oneWay',windows=windows,settings=s,
        instructions='Flexible Trip.com search per window; flexible search has no baggage/nonstop filters. Verify shortlisted dates with exact search and hasCarryOnBaggage=true. Submit actual structured results. Never assume currency or baggage. No automatic booking.')
def refresh():
    command=os.environ.get('FLIGHT_PROVIDER_COMMAND')
    if not command: raise ValueError('Unattended provider not configured. Import agent results or configure a supported adapter. ChatGPT plugins are not public APIs.')
    argv=json.loads(command)
    if not isinstance(argv,list) or not argv or not all(isinstance(v,str) for v in argv): raise ValueError('Provider command must be a JSON argv array.')
    p=subprocess.run(argv,input=json.dumps(plan()),text=True,capture_output=True,timeout=180)
    if p.returncode: raise ValueError('Provider failed; existing results retained. Check adapter logs locally.')
    if len(p.stdout)>2_000_000: raise ValueError('Provider output exceeds 2 MB.')
    batch=json.loads(p.stdout)
    if not isinstance(batch,list) or len(batch)>100: raise ValueError('Provider must return an array of at most 100 structured results.')
    for result in batch: normalize(result)
    return [ingest(result) for result in batch]
def scheduler_tick(local=None):
    local=local or datetime.now(TZ); s=state()['settings']
    if s['paused'] or not s['start']<=local.date().isoformat()<=s['end']: return []
    tasks=[]; results=[]
    if local.hour>=s['daily_hour']: tasks.append(('daily-'+local.date().isoformat(),refresh))
    if local.weekday()==4 and local.hour>=s['weekly_hour']: tasks.append(('weekly-'+local.date().isoformat(),report))
    for jid,fn in tasks:
        with connect() as c:
            if not c.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?)',(jid,'running',now())).rowcount: continue
        try: fn(); status='success'
        except Exception as e: status='failed: '+str(e)[:300]
        with connect() as c: c.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(status,now(),jid))
        results.append(dict(id=jid,status=status))
    return results

def serve(port):
    token=os.environ.get('FLIGHT_AGENT_TOKEN') or secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def response(self,status,v,mime='application/json'):
            body=json.dumps(v).encode() if mime=='application/json' else v
            self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(body)
        def trusted(self): return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')
        def do_GET(self):
            if not self.trusted(): return self.response(403,dict(error='Invalid host'))
            path=urlparse(self.path).path
            if path=='/api/state': return self.response(200,state())
            if path=='/api/session': return self.response(200,dict(csrf=csrf))
            if path=='/api/plan': return self.response(200,plan())
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            if path in files:
                f,mime=files[path]; return self.response(200,(ROOT/'web'/f).read_bytes(),mime)
            return self.response(404,dict(error='Not found'))
        def do_POST(self):
            if not self.trusted(): return self.response(403,dict(error='Invalid host'))
            agent=hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
            browser=self.headers.get('Origin') in (f'http://127.0.0.1:{port}',f'http://localhost:{port}') and hmac.compare_digest(self.headers.get('X-CSRF-Token',''),csrf)
            if not agent and not browser: return self.response(403,dict(error='Authorised agent or same-origin session required.'))
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<=2_000_000: raise ValueError('Maximum payload: 2 MB.')
                data=json.loads(self.rfile.read(length) or b'{}'); path=urlparse(self.path).path
                if path=='/api/import': result=ingest(data)
                elif path=='/api/settings':
                    validate_settings(data)
                    with connect() as c: c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(data),))
                    result=dict(saved=True)
                elif path=='/api/report': result=report()
                elif path=='/api/refresh':
                    if not agent: return self.response(403,dict(error='Refresh requires an authorised agent; import its results here.'))
                    result=refresh()
                else: return self.response(404,dict(error='Not found'))
                return self.response(200,result)
            except (ValueError,KeyError,TypeError,IndexError,subprocess.TimeoutExpired) as e: return self.response(400,dict(error=str(e)))
    print(f'Dashboard: http://127.0.0.1:{port}',flush=True)
    print('Set FLIGHT_AGENT_TOKEN to enable a stable agent API token.',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('command',choices=['serve','state','plan','import','report','refresh','scheduler']); p.add_argument('file',nargs='?'); p.add_argument('--port',type=int,default=8765); a=p.parse_args()
    try:
        if a.command=='serve': serve(a.port)
        elif a.command=='scheduler':
            print('Scheduler running in Brisbane time; stop with Ctrl+C.',flush=True)
            while True:
                for r in scheduler_tick(): print(json.dumps(r),flush=True)
                time.sleep(30)
        else:
            if a.command=='import':
                if not a.file: p.error('import requires a JSON file or - for stdin')
                result=ingest(json.loads(sys.stdin.read() if a.file=='-' else Path(a.file).read_text()))
            else: result=dict(state=state,plan=plan,report=report,refresh=refresh)[a.command]()
            print(json.dumps(result,indent=2))
    except (ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as e:
        print(json.dumps(dict(error=str(e))),file=sys.stderr); return 1
    return 0
if __name__=='__main__': sys.exit(main())
