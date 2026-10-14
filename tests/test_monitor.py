import copy, json, tempfile, unittest, sys, subprocess, os, threading, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
FIXTURE=json.loads((app.ROOT/'examples'/'initial-trip-result.json').read_text())
class Monitor(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=app.DB; app.DB=Path(self.tmp.name)/'test.sqlite'
        self.p=copy.deepcopy(FIXTURE); self.p['observed_at']=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
    def tearDown(self): app.DB=self.old; self.tmp.cleanup()
    def altered(self,price=170,run='second'):
        p=copy.deepcopy(self.p); p['run_id']=run; p['observed_at']=app.now(); p['structuredContent']['flight']['flights'][0]['price']=price; return p
    def test_real_fixture_and_duplicate(self):
        self.assertEqual(app.ingest(self.p)['imported'],5); self.assertTrue(app.ingest(self.p)['duplicate'])
        s=app.state(); self.assertEqual(len(s['fares']),4); self.assertEqual(s['fares'][0]['price'],187)
    def test_price_drop_persisted_and_deduplicated(self):
        app.ingest(self.p); r=app.ingest(self.altered()); self.assertEqual(len(r['alerts']),1)
        self.assertIn('down',r['alerts'][0]); self.assertEqual(len(app.state()['history']),2)
        self.assertEqual(app.ingest(self.altered(run='third'))['alerts'],[])
    def test_currency_never_mixed(self):
        p=self.altered(); p['structuredContent']['flight']['flights'][0]['currency']='AUD'; app.ingest(p)
        self.assertNotIn(170,[f['price'] for f in app.state()['fares']])
    def test_unknown_baggage_excluded(self):
        del self.p['structuredContent']['flight']['flights'][0]['journalList'][0]['journal']['baggageAllowance']; app.ingest(self.p)
        self.assertEqual(len(app.state()['fares']),3)
    def test_failed_search_retains_data(self):
        app.ingest(self.p); p=self.altered(); p['isError']=True
        with self.assertRaises(ValueError): app.ingest(p)
        self.assertEqual(len(app.state()['fares']),4)
    def test_nan_price_and_wrong_route_rejected_atomically(self):
        for field,value in [('price',float('nan')),('route','SYD')]:
            p=self.altered()
            if field=='price': p['structuredContent']['flight']['flights'][-1]['price']=value
            else: p['structuredContent']['flight']['flights'][-1]['journalList'][0]['journal']['departCity']=value
            with self.assertRaises(ValueError): app.ingest(p)
        self.assertEqual(app.state()['runs'],[])
    def test_late_backfill_does_not_replace_current_or_alert(self):
        app.ingest(self.altered()); p=self.altered(100,'backfill'); p['observed_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.assertEqual(app.ingest(p)['alerts'],[]); self.assertEqual(app.state()['fares'][0]['price'],170)
    def test_stale_and_reports(self):
        self.p['observed_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(); app.ingest(self.p)
        self.assertTrue(all(f['stale'] for f in app.state()['fares'])); self.assertIn('stale',app.report()['text'])
    def test_plan_chunks(self):
        for w in app.plan()['windows']: self.assertEqual(w['start'][:7],w['end'][:7])
    def test_no_fake_provider_success(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError): app.refresh()
    def test_scheduler_once_pause_and_end(self):
        t=datetime(2026,10,16,9,tzinfo=app.TZ)
        with patch.object(app,'refresh',return_value=[]):
            self.assertEqual(len(app.scheduler_tick(t)),2); self.assertEqual(app.scheduler_tick(t),[])
            self.assertEqual(app.scheduler_tick(datetime(2026,12,15,9,tzinfo=app.TZ)),[])
        with app.connect() as c:
            s=app.settings(c); s['paused']=True; c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(s),))
        self.assertEqual(app.scheduler_tick(datetime(2026,10,17,9,tzinfo=app.TZ)),[])
    def test_link_safety_and_threshold_validation(self):
        self.assertIsNone(app.safe_url('https://trip.com.attacker.test/x')); self.assertIsNone(app.safe_url('javascript:alert(1)'))
        v=copy.deepcopy(app.DEFAULT); v['target']=float('inf')
        with self.assertRaises(ValueError): app.validate_settings(v)
    def test_mcp_protocol_and_actual_import(self):
        env=dict(os.environ,FLIGHT_DB=str(app.DB)); request=[dict(jsonrpc='2.0',id=1,method='initialize',params={}),dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='import_trip_result',arguments=dict(result=self.p)))]
        p=subprocess.run([sys.executable,str(app.ROOT/'mcp_server.py')],input='\n'.join(json.dumps(r) for r in request)+'\n',text=True,capture_output=True,env=env,check=True)
        result=[json.loads(line) for line in p.stdout.splitlines()]; self.assertFalse(result[1]['result']['isError']); self.assertEqual(len(app.state()['fares']),4)

class HttpSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import socket
        cls.tmp=tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); cls.port=sock.getsockname()[1]
        cls.url=f'http://127.0.0.1:{cls.port}'
        env=dict(os.environ,FLIGHT_DB=str(Path(cls.tmp.name)/'http.sqlite'),FLIGHT_AGENT_TOKEN='test-only-token')
        cls.proc=subprocess.Popen([sys.executable,str(app.ROOT/'app.py'),'serve','--port',str(cls.port)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.url+'/api/session',timeout=1) as r: cls.csrf=json.load(r)['csrf']
                break
            except OSError: __import__('time').sleep(.02)
        else: raise RuntimeError('Server failed to start')
    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.communicate(timeout=3); cls.tmp.cleanup()
    def post(self,path,data,headers):
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode(),headers=dict({'Content-Type':'application/json'},**headers))
        with urllib.request.urlopen(req,timeout=2) as r: return json.load(r)
    def test_untrusted_write_denied_and_agent_import_allowed(self):
        with self.assertRaises(urllib.error.HTTPError) as e: self.post('/api/report',{}, {})
        self.assertEqual(e.exception.code,403)
        r=self.post('/api/import',FIXTURE,{'Authorization':'Bearer test-only-token'})
        self.assertEqual(r['imported'],5)
    def test_browser_origin_and_host_controls(self):
        with self.assertRaises(urllib.error.HTTPError): self.post('/api/report',{}, {'Origin':'https://attacker.example','X-CSRF-Token':self.csrf})
        self.assertIn('text',self.post('/api/report',{}, {'Origin':self.url,'X-CSRF-Token':self.csrf}))
        req=urllib.request.Request(self.url+'/api/state',headers={'Host':'attacker.example'})
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(req)
        self.assertEqual(e.exception.code,403)

if __name__=='__main__': unittest.main()
