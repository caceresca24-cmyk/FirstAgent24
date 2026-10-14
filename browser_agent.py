"""Conservative Playwright browser runner. Never book or bypass verification.
Live Trip.com DOM selectors require verification on a reachable page.
"""
import argparse, asyncio, hashlib, json, os, re, sys, uuid
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import app

DEFAULT_SELECTORS={'cards':'[data-testid="flight-card"], [class*="flight-item"], [class*="FlightItem"], article', 'text':'body'}
class ExtractionError(ValueError): pass

def search_url(pair,currency):
    return 'https://www.trip.com/flights/showfarefirst?'+urlencode(dict(dcity='BNE',acity='DPS',ddate=pair['depart'],rdate=pair['return_date'],triptype='RT',**{'class':'Y'},quantity=1,childqty=0,babyqty=0,curr=currency,locale='en-AU'))

def blockage(text):
    # Do not classify the normal page's generic Sign in link as a login wall.
    patterns=[('verification_required',r'captcha|verify (?:that )?you are human|security verification|slide to verify|unusual traffic|access denied'),
              ('login_required',r'sign in to (?:continue|view)|log in to (?:continue|view)|session (?:has )?expired')]
    for status,pattern in patterns:
        if re.search(pattern,text,re.I): return status
    return None

def parse_card(text,pair,currency,url):
    """Require a complete priced return itinerary; reject ambiguous one-way cards.
    Accepts explicit ISO dates and timestamps in visible rendered card text.
    No inferred baggage and no sum of unrelated leg prices.
    """
    if not re.search(r'round[- ]trip|return (?:trip|fare)|total',text,re.I): raise ExtractionError('No explicit return-trip total.')
    routes=re.findall(r'\b(BNE|DPS)\b\s*(?:→|->|to|–|-)\s*\b(BNE|DPS)\b',text,re.I)
    if [(a.upper(),b.upper()) for a,b in routes]!=[('BNE','DPS'),('DPS','BNE')]: raise ExtractionError('Both reciprocal route legs must be visible.')
    stamps=re.findall(r'\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\b',text)
    flights=re.findall(r'\b([A-Z]{2}\s?\d{2,4})\b',text)
    flights=list(dict.fromkeys(v.replace(' ','') for v in flights))
    if len(stamps)!=4 or len(flights)!=2: raise ExtractionError('Complete outbound/return dates, times and flight numbers required.')
    if stamps[0][:10]!=pair['depart'] or stamps[2][:10]!=pair['return_date']: raise ExtractionError('Displayed dates do not match this search.')
    if len(re.findall(r'\bnon[- ]?stop\b|\b0 stops\b',text,re.I))<2: raise ExtractionError('Nonstop status must be explicit on both legs.')
    quoted=re.findall(r'\b(AUD|USD|CNY|EUR|GBP|SGD|IDR)\s*([0-9][0-9,]*(?:\.\d{1,2})?)',text)
    if len(quoted)!=1 or quoted[0][0]!=currency: raise ExtractionError('Exactly one total in the requested currency is required.')
    price=float(quoted[0][1].replace(',',''))
    carry=re.findall(r'(?:carry[- ]on|cabin bag(?:gage)?)(?:\s*:\s*|\s+)(included|not included|excluded|unconfirmed|unknown)',text,re.I)
    checked=re.findall(r'checked (?:bag(?:gage)?)(?:\s*:\s*|\s+)(included|not included|excluded|unconfirmed|unknown)',text,re.I)
    def flag(values,i):
        if len(values)!=2: return None
        return True if values[i].lower()=='included' else False if values[i].lower() in ('not included','excluded') else None
    journals=[]
    for i,(origin,dest,tz1,tz2) in enumerate([('BNE','DPS','Australia/Brisbane','Asia/Makassar'),('DPS','BNE','Asia/Makassar','Australia/Brisbane')]):
        dep=datetime.fromisoformat(stamps[i*2]); arr=datetime.fromisoformat(stamps[i*2+1])
        duration=int((arr.replace(tzinfo=ZoneInfo(tz2)).timestamp()-dep.replace(tzinfo=ZoneInfo(tz1)).timestamp())/60)
        if not 240<=duration<=600: raise ExtractionError('Invalid nonstop duration; verify displayed dates and times.')
        journals.append(dict(journal=dict(departCity=origin,arriveCity=dest,departTime=dep.isoformat(' '),arriveTime=arr.isoformat(' '),duration=duration,isTransfer=False,cabinClass='Y',baggageAllowance=dict(hasCarryOn=flag(carry,i),hasCheckedBaggage=flag(checked,i))),segments=[dict(flightNo=flights[i],airlineName=flights[i][:2],isStop=False)]))
    return dict(price=price,currency=currency,journalList=journals,deeplinkUrls=dict(onlineUrl=url))

async def collect(page,pair,currency,selectors):
    text=await page.locator(selectors['text']).inner_text()
    blocked=blockage(text)
    if blocked: raise ExtractionError(blocked+': complete verification yourself in the agent browser, then resume.')
    cards=page.locator(selectors['cards']); count=await cards.count()
    if count==0:
        # Never convert an unsupported page or still-loading page into empty success.
        raise ExtractionError('No recognisable result cards; live selector verification required.')
    offers=[]; errors=[]
    for i in range(count):
        try: offers.append(parse_card(await cards.nth(i).inner_text(),pair,currency,search_url(pair,currency)))
        except ExtractionError as e: errors.append(str(e))
    # Partial extraction is explicit. It cannot certify all available flights.
    if errors: raise ExtractionError(f'{len(errors)}/{count} cards unreadable: '+errors[0])
    if not offers: raise ExtractionError('No complete round-trip offers extracted.')
    return dict(run_id=str(uuid.uuid4()),observed_at=app.now(),flight=dict(success=True,tripType='roundTrip',queryDepartDate=pair['depart'],queryReturnDate=pair['return_date'],rawQuery='Browser search BNE ↔ DPS, one adult economy',flights=offers)),count

def write_status(value):
    path=app.DB.parent/'browser-status.json'; path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps({**value,'updated':app.now()},indent=2),encoding='utf-8'); temporary.replace(path)

async def capture_cards(page,selectors,pair,currency):
    """Capture result-card text and button labels, never cookies or full-page HTML."""
    cards=page.locator(selectors['cards']); items=[]
    for i in range(min(await cards.count(),20)):
        card=cards.nth(i)
        items.append(dict(text=(await card.inner_text())[:6000],
            tag=await card.evaluate('(e)=>e.tagName'),
            buttons=await card.locator('button,[role="button"]').all_text_contents()))
    return dict(purpose='Flight-card layout diagnosis only; not imported prices',pair=pair,
        currency=currency,card_count=await cards.count(),cards=items,
        note='Review before sharing. No cookies, passwords, storage, page HTML or account header are collected.')

async def run_scan(page,pairs,currency,selectors,limit,start_index=0,delay=15,save_checkpoint=None):
    completed=0
    for index in range(start_index,min(len(pairs),start_index+limit)):
        pair=pairs[index]
        write_status(dict(status='running',pair=pair,completed=completed,total_pairs=len(pairs),next_index=index))
        try:
            response=await page.goto(search_url(pair,currency),wait_until='domcontentloaded',timeout=45000)
            if response and response.status>=400: raise ExtractionError(f'Trip.com returned HTTP {response.status}.')
            # Wait for a supported result card OR an explicit verification wall.
            await page.wait_for_function("s => document.querySelector(s) || /captcha|verify you are human|sign in to continue|access denied/i.test(document.body.innerText)",arg=selectors['cards'],timeout=30000)
            # Load additional currently available cards, bounded to avoid runaway browsing.
            previous=-1
            for _ in range(8):
                count=await page.locator(selectors['cards']).count()
                if count==previous: break
                previous=count; await page.mouse.wheel(0,1200); await page.wait_for_timeout(1000)
            payload,count=await collect(page,pair,currency,selectors)
            result=app.ingest(payload,source='Trip.com browser')
            completed+=1
            if save_checkpoint: save_checkpoint(index+1)
            print(json.dumps(dict(pair=pair,**result)),flush=True)
            write_status(dict(status='partial',completed=completed,total_pairs=len(pairs),next_index=index+1,
                message='Only complete rendered cards are captured; market-wide coverage is not guaranteed.'))
        except Exception as e:
            write_status(dict(status='blocked',pair=pair,completed=completed,total_pairs=len(pairs),next_index=index,message=str(e)[:500]))
            raise ExtractionError(str(e)) from e
        if index+1<min(len(pairs),start_index+limit): await asyncio.sleep(delay)
    return completed

async def browser_main(args):
    from playwright.async_api import async_playwright
    plan=app.plan(); pairs=plan['pairs']; s=plan['settings']
    if args.command in ('scan','diagnose') and (s['paused'] or s['trip_type']!='roundTrip' or not pairs): raise ExtractionError('Enable an unexpired return-trip watch first.')
    profile=Path(args.profile).resolve(); profile.mkdir(parents=True,exist_ok=True)
    os.chmod(profile,0o700)
    selectors=dict(DEFAULT_SELECTORS)
    if args.selectors:
        configured=json.loads(Path(args.selectors).read_text())
        if set(configured)!=set(selectors) or not all(isinstance(v,str) and v for v in configured.values()): raise ExtractionError('Selectors file must contain nonempty cards and text selectors.')
        selectors.update(configured)
    identity=hashlib.sha256(json.dumps(dict(pairs=pairs,currency=s['currency'],selectors=selectors),sort_keys=True).encode()).hexdigest()
    checkpoint=profile/'scan-checkpoint.json'; start=0
    if args.resume and checkpoint.exists():
        cp=json.loads(checkpoint.read_text())
        if cp.get('identity')!=identity: raise ExtractionError('Search plan changed. Start without --resume.')
        start=cp['next_index']
    def save(index):
        temp=checkpoint.with_suffix('.tmp'); temp.write_text(json.dumps(dict(identity=identity,next_index=index))); temp.replace(checkpoint)
    launch=dict(headless=args.command not in ('login','diagnose') and not args.headed)
    if args.executable: launch['executable_path']=args.executable
    # Preserve configured proxy policy when the environment uses one.
    proxy=os.environ.get('HTTPS_PROXY')
    if proxy: launch['proxy']=dict(server=proxy)
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(profile),**launch)
        try:
            page=context.pages[0] if context.pages else await context.new_page()
            if args.command=='login':
                await page.goto('https://www.trip.com/',timeout=45000)
                await asyncio.to_thread(input,'Sign in yourself in this browser, then press Enter here to save the session. ')
            elif args.command=='diagnose':
                pair=pairs[0]
                await page.goto(search_url(pair,s['currency']),wait_until='domcontentloaded',timeout=45000)
                await asyncio.to_thread(input,'Wait for the flight results to appear. Leave the results visible, then press Enter here to capture flight-card text only. ')
                data=await capture_cards(page,selectors,pair,s['currency'])
                path=app.DB.parent/'flight-card-diagnostics.json';path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                print(f'Saved {data["card_count"]} card(s) to {path}. Review the file before sharing; no flight observations were imported.',flush=True)
            else:
                await run_scan(page,pairs,s['currency'],selectors,args.max_pairs,start,args.delay,save)
        finally: await context.close()

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('command',choices=['login','scan','plan','diagnose'])
    p.add_argument('--profile',default=str(app.ROOT/'browser_data')); p.add_argument('--headed',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--max-pairs',type=int,default=1);p.add_argument('--delay',type=float,default=15);p.add_argument('--selectors');p.add_argument('--executable')
    args=p.parse_args()
    if not 1<=args.max_pairs<=500 or args.delay<10: p.error('Use 1–500 pairs and at least 10 seconds between searches.')
    if args.command=='plan': print(json.dumps(app.plan(),indent=2));return 0
    try: asyncio.run(browser_main(args));return 0
    except (Exception,KeyboardInterrupt) as e:
        print('Browser agent stopped: '+str(e),file=sys.stderr)
        try: write_status(dict(app.browser_status(),status='blocked',message=str(e)[:500]))
        except Exception as status_error: print('Could not save browser status: '+str(status_error),file=sys.stderr)
        return 1
if __name__=='__main__':sys.exit(main())
