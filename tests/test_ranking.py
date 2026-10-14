import copy,json,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,browser_agent as agent
from test_browser_agent import TEXT,PAIR
class Rankings(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=app.DB;app.DB=Path(self.tmp.name)/'test.sqlite'
        with app.connect() as c:
            s=dict(app.DEFAULT,currency='AUD');c.execute('UPDATE settings SET value=? WHERE id=1',(json.dumps(s),))
    def tearDown(self):app.DB=self.old;self.tmp.cleanup()
    def insert(self,price,currency='AUD',checked=False,flight='OD158'):
        fare=agent.parse_card(TEXT,PAIR,'AUD',agent.search_url(PAIR,'AUD'))
        fare['price']=price;fare['currency']=currency
        fare['journalList'][0]['segments'][0]['flightNo']=flight
        for leg in fare['journalList']:leg['journal']['baggageAllowance']['hasCheckedBaggage']=checked
        app.ingest(dict(flight=dict(success=True,tripType='roundTrip',flights=[fare])))
    def test_rank_prices_within_currency_not_across_currencies(self):
        self.insert(1500);self.insert(1200,flight='OD159');self.insert(100,'USD',flight='OD160')
        s=app.state();self.assertEqual([f['rank'] for f in s['comparison']],[1,2,1])
        self.assertEqual(s['lowest_observed']['currency'],'AUD');self.assertEqual(s['best_match']['price'],1200)
        self.assertIn('current search uses AUD',s['comparison'][-1]['reasons'][-1])
    def test_only_nonqualifying_quotes_still_ranked_without_recommendation(self):
        self.insert(1300,'USD',True);self.insert(1100,'USD',True,flight='OD159')
        s=app.state();self.assertIsNone(s['best_match']);self.assertEqual(s['lowest_observed']['price'],1100)
        self.assertFalse(s['lowest_observed']['qualifies']);self.assertIn('Checked baggage included',s['lowest_observed']['reasons'])
    def test_unknown_baggage_explicitly_flagged_in_comparison(self):
        self.insert(800,checked=None)
        s=app.state();self.assertEqual(s['fares'],[]);self.assertIn('Checked baggage unknown',s['comparison'][0]['reasons'])
    def test_alert_target_does_not_hide_higher_priced_matches_or_change_identity(self):
        self.insert(2500)
        p=app.plan();changed=copy.deepcopy(p);changed['settings']['target']=1
        self.assertEqual(agent.search_identity(p,agent.DEFAULT_SELECTORS),agent.search_identity(changed,agent.DEFAULT_SELECTORS))
        self.assertEqual(len(app.state()['fares']),1)
        changed['settings']['currency']='USD'
        self.assertNotEqual(agent.search_identity(p,agent.DEFAULT_SELECTORS),agent.search_identity(changed,agent.DEFAULT_SELECTORS))
