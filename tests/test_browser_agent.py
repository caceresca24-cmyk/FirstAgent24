"""Synthetic DOM fixtures test mechanics, not Trip.com's live selectors."""
import copy, json, os, tempfile, unittest, sys, importlib.util
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app, browser_agent as agent
PAIR=dict(depart='2026-11-08',return_date='2026-11-15',stay_days=7)
TEXT='''Round-trip total AUD 450.00
BNE → DPS OD158
2026-11-08 07:15 2026-11-08 11:30 Nonstop
Carry-on included
Checked baggage not included
DPS → BNE OD157
2026-11-15 22:00 2026-11-16 06:15 Nonstop
Carry-on included
Checked baggage not included'''
class ReturnTrips(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=app.DB; app.DB=Path(self.tmp.name)/'test.sqlite'
        with app.connect() as c:
            s=dict(app.DEFAULT,currency='AUD'); c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(s),))
    def tearDown(self): app.DB=self.old;self.tmp.cleanup()
    def payload(self,text=TEXT):
        return dict(flight=dict(success=True,tripType='roundTrip',flights=[agent.parse_card(text,PAIR,'AUD',agent.search_url(PAIR,'AUD'))]))
    def test_complete_return_import_and_total(self):
        app.ingest(self.payload());f=app.state()['fares'][0]
        self.assertEqual(f['price'],450);self.assertEqual(f['return_date'],'2026-11-15');self.assertEqual(f['stay_days'],7)
        self.assertEqual(len(f['legs']),2);self.assertFalse(f['checked']);self.assertTrue(f['carry_on'])
    def test_checked_inclusion_on_either_leg_excluded(self):
        app.ingest(self.payload(TEXT.replace('Checked baggage not included','Checked baggage included',1)))
        self.assertEqual(app.state()['fares'],[])
    def test_unknown_baggage_flagged_not_guessed(self):
        app.ingest(self.payload(TEXT.replace('Checked baggage not included','Checked baggage unknown',1)))
        self.assertEqual(app.state()['fares'],[]); self.assertEqual(len(app.state()['verification']),1)
    def test_oneway_card_or_wrong_dates_or_currency_rejected(self):
        for text in [TEXT.split('DPS → BNE')[0],TEXT.replace('2026-11-15','2026-11-14'),TEXT.replace('AUD 450','USD 450')]:
            with self.assertRaises(agent.ExtractionError): self.payload(text)
    def test_missing_nonstop_not_assumed(self):
        with self.assertRaises(agent.ExtractionError): self.payload(TEXT.replace('Nonstop','1 stop',1))
    def test_checked_status_changes_are_separate_fares(self):
        app.ingest(self.payload()); app.ingest(self.payload(TEXT.replace('Checked baggage not included','Checked baggage included',1)))
        self.assertEqual(len(app.state()['fares']),1)
    def test_every_departure_has_four_stays(self):
        p=app.plan(); pairs=p['pairs'];self.assertGreater(len(pairs),0)
        self.assertEqual(len(pairs)%4,0);self.assertEqual(pairs[-1]['depart'],'2026-12-15');self.assertEqual(pairs[-1]['return_date'],'2026-12-25')
    def test_login_and_verification_detection(self):
        self.assertEqual(agent.blockage('Please verify you are human'),'verification_required')
        self.assertEqual(agent.blockage('Sign in to continue'),'login_required');self.assertIsNone(agent.blockage('Sign in | Flight results'))

    def test_status_can_be_updated_after_failure(self):
        agent.write_status(dict(status='running'))
        old=app.browser_status()
        agent.write_status(dict(old,status='blocked',message='unreadable card'))
        self.assertEqual(app.browser_status()['message'],'unreadable card')

    def test_main_reports_original_failure_without_traceback(self):
        import io,contextlib
        from unittest.mock import AsyncMock
        agent.write_status(dict(status='blocked',next_index=2))
        error=io.StringIO()
        with patch.object(sys,'argv',['browser_agent.py','scan']),patch.object(agent,'browser_main',new=AsyncMock(side_effect=agent.ExtractionError('Both reciprocal route legs must be visible.'))),contextlib.redirect_stderr(error):
            self.assertEqual(agent.main(),1)
        self.assertIn('Both reciprocal route legs',error.getvalue())
        self.assertNotIn('Traceback',error.getvalue());self.assertEqual(app.browser_status()['next_index'],2)

@unittest.skipUnless(importlib.util.find_spec('playwright'),'Optional Playwright not installed')
class BrowserFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DB;app.DB=Path(self.tmp.name)/'browser.sqlite'
        with app.connect() as c:
            s=dict(app.DEFAULT,currency='AUD');c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(s),))
        self.pw=await async_playwright().start()
        kwargs=dict(headless=True)
        if Path('/usr/bin/chromium').exists(): kwargs.update(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        self.browser=await self.pw.chromium.launch(**kwargs);self.page=await self.browser.new_page()
    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop();app.DB=self.old;self.tmp.cleanup()
    async def test_real_dom_to_database(self):
        await self.page.set_content('<article>'+TEXT.replace('\n','<br>')+'</article>')
        payload,count=await agent.collect(self.page,PAIR,'AUD',agent.DEFAULT_SELECTORS)
        app.ingest(payload,source='Trip.com browser');self.assertEqual(count,1);self.assertEqual(app.state()['fares'][0]['price'],450)

    async def test_diagnostics_collect_only_card_text_and_controls(self):
        await self.page.set_content('<header>Private account header</header><article>'+TEXT.replace('\n','<br>')+'<button>Select outbound</button></article>')
        data=await agent.capture_cards(self.page,agent.DEFAULT_SELECTORS,PAIR,'AUD')
        self.assertEqual(data['card_count'],1)
        self.assertNotIn('Private account header',json.dumps(data));self.assertEqual(data['cards'][0]['buttons'],['Select outbound'])
        self.assertEqual(app.state()['runs'],[])
    async def test_verification_preserves_previous_results(self):
        await self.page.set_content('<article>'+TEXT.replace('\n','<br>')+'</article>')
        payload,_=await agent.collect(self.page,PAIR,'AUD',agent.DEFAULT_SELECTORS);app.ingest(payload)
        await self.page.set_content('<h1>Verify you are human</h1>')
        with self.assertRaises(agent.ExtractionError):await agent.collect(self.page,PAIR,'AUD',agent.DEFAULT_SELECTORS)
        self.assertEqual(len(app.state()['fares']),1)
    async def test_unsupported_page_never_empty_success(self):
        await self.page.set_content('<p>Loading flights...</p>')
        with self.assertRaises(agent.ExtractionError):await agent.collect(self.page,PAIR,'AUD',agent.DEFAULT_SELECTORS)
    async def test_partial_cards_stop_instead_of_claiming_all(self):
        await self.page.set_content('<article>'+TEXT.replace('\n','<br>')+'</article><article>One-way AUD 100</article>')
        with self.assertRaises(agent.ExtractionError):await agent.collect(self.page,PAIR,'AUD',agent.DEFAULT_SELECTORS)
    async def test_scan_navigation_checkpoint_and_status(self):
        # Intercept a normal browser navigation with a labelled synthetic fixture.
        await self.page.route('https://www.trip.com/**',lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<article>'+TEXT.replace('\n','<br>')+'</article>'))
        checkpoint=[]
        self.assertEqual(await agent.run_scan(self.page,[PAIR],'AUD',agent.DEFAULT_SELECTORS,1,save_checkpoint=checkpoint.append),1)
        self.assertEqual(checkpoint,[1]);self.assertEqual(app.browser_status()['status'],'partial')
        self.assertEqual(app.state()['fares'][0]['source'],'Trip.com browser')
    async def test_blocked_navigation_does_not_advance_checkpoint(self):
        await self.page.route('https://www.trip.com/**',lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<h1>Verify you are human</h1>'))
        checkpoint=[]
        with self.assertRaises(agent.ExtractionError):await agent.run_scan(self.page,[PAIR],'AUD',agent.DEFAULT_SELECTORS,1,save_checkpoint=checkpoint.append)
        self.assertEqual(checkpoint,[]);self.assertEqual(app.browser_status()['next_index'],0);self.assertEqual(app.state()['runs'],[])
    async def test_observed_outbound_layout_selects_virgin_and_stops_for_return_verification(self):
        import html
        texts=json.loads((app.ROOT/'tests'/'fixtures'/'trip_outbound_cards.json').read_text(encoding='utf-8'))
        markup=''.join('<article>'+html.escape(t).replace('\n','<br>')+'<button>Select</button></article>' for t in texts)
        returned=texts[1].replace('BNE','TMP').replace('DPS','BNE').replace('TMP','DPS')
        await self.page.set_content(markup)
        # A selected Jetstar would fail: it explicitly includes checked baggage.
        await self.page.locator('article').nth(1).locator('button').evaluate('(b,html)=>b.onclick=()=>{document.body.innerHTML=html}', '<article>'+html.escape(returned).replace('\n','<br>')+'</article>')
        with self.assertRaisesRegex(agent.ExtractionError,'Outbound selected'):
            await agent.collect(self.page,PAIR,'USD',agent.DEFAULT_SELECTORS)
        diagnostic=json.loads((app.DB.parent/'flight-card-diagnostics.json').read_text(encoding='utf-8'))
        self.assertEqual(diagnostic['selected_outbound']['index'],1)
        self.assertIsNone(diagnostic['selected_outbound']['leg']['checked'])
        self.assertEqual(diagnostic['cards'][0]['leg']['origin'],'DPS')
        self.assertEqual(app.state()['runs'],[])
    async def test_known_card_labels_do_not_infer_baggage(self):
        texts=json.loads((app.ROOT/'tests'/'fixtures'/'trip_outbound_cards.json').read_text(encoding='utf-8'))
        legs=[agent.parse_selection_card(t) for t in texts]
        self.assertTrue(legs[0]['checked']);self.assertIsNone(legs[1]['checked']);self.assertIsNone(legs[1]['carry_on'])
        self.assertEqual(sum(v['nonstop'] for v in legs),2)
        self.assertFalse(legs[3]['nonstop']);self.assertTrue(legs[3]['carry_on'])
