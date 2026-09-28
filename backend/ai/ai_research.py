"""Parallel, bounded technical research with source-type labeling."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from urllib.parse import urlparse
import urllib.request
from backend.ai.maintenance_ai import tokens, COMPONENTS

OFFICIAL=('cummins.com','freightliner.com','dtnatechlit.com','dtnacontent-dtna.prd.freightliner.com','volvotrucks.us','volvotrucks.com','macktrucks.com','peterbilt.com','kenworth.com','paccar.com','thermoking.com','carrier.com','bendix.com','zf.com','meritor.com','alcoawheels.com','accuridecorp.com','michelintruck.com','nhtsa.gov')
TECHNICAL=set('air pressure low high leak crack broken fractured overheating temperature oil coolant fuel diesel exhaust engine turbo boost underboost vibration noise grinding squeal clicking knocking wheel rim flange barrel lug nut stud hub bearing tire bead brake abs steering suspension compressor alternator battery starter electrical voltage wiring sensor connector fuse relay harness def dpf scr regen code fault alarm reefer refrigeration cooling belt radiator thermostat fan pump valve clutch transmission differential axle seal filter egr drier dryer solenoid throttle actuator torque no start crank smoke white black blue intermittent stopped running idling hot cold replaced missing bent deformation flat blowout warning power loss charge charging discharge refrigerant suction discharge frozen evaporator condenser trip reset coolant'.split())
BRANDS=set('freightliner cascadia volvo mack peterbilt kenworth international navistar detroit cummins paccar thermoking thermo king carrier bendix wabco eaton allison meritor haldex maxxforce daikin evolution vector precedent apu tripac'.split())

def technical_query(query):
    # Public search receives maintenance terms/model codes, not the raw chat or transcript.
    words=tokens(query)
    terms=sorted(words & (TECHNICAL|BRANDS|COMPONENTS))
    codes=re.findall(r'\b(?:SPN\s*\d{1,6}(?:\s*FMI\s*\d{1,3})?|FMI\s*\d{1,3}|[PBCU]\d{4}|(?:DD|ISX|X|D)\d{1,3})\b',str(query),re.I)
    return ' '.join(terms+codes)[:260]

def source_type(url):
    host=(urlparse(url).hostname or '').lower()
    if any(host==d or host.endswith('.'+d) for d in OFFICIAL):return 'manufacturer / official'
    if host=='reddit.com' or host.endswith('.reddit.com') or any(x in host for x in ('forum','truckersreport','truckersnetwork')):return 'community discussion - unverified'
    return 'independent technical source - verify applicability'

def search_research(query,limit=7):
    key=os.getenv('SERPER_API_KEY','').strip();q=technical_query(query)
    if not key:return {'status':'not_configured','results':[]}
    if not q:return {'status':'needs_specific_symptom','results':[]}
    queries=[q+' truck service manual troubleshooting',q+' truck mechanic diagnosis repair',q+' (site:reddit.com OR site:thetruckersreport.com)']
    def search(value):
        request=urllib.request.Request('https://google.serper.dev/search',data=json.dumps({'q':value,'gl':'us','hl':'en','num':4}).encode(),headers={'X-API-KEY':key,'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(request,timeout=7) as response:return json.load(response).get('organic',[]),True
        except Exception:return [],False
    with ThreadPoolExecutor(max_workers=3) as pool:responses=list(pool.map(search,queries))
    seen=set();results=[]
    for group,ok in responses:
        for item in group:
            url=str(item.get('link') or '')
            if not url.startswith('https://') or url in seen:continue
            seen.add(url);results.append({'url':url,'title':str(item.get('title') or '')[:180],
                'snippet':str(item.get('snippet') or '')[:1000],'source_type':source_type(url),
                'evidence_level':'search snippet only; full page not verified'})
    results.sort(key=lambda item:0 if item['source_type']=='manufacturer / official' else 2 if item['source_type'].startswith('community') else 1)
    # Reserve one community result when available without displacing official results entirely.
    selected=results[:limit]
    discussion=next((x for x in results if x['source_type'].startswith('community')),None)
    if discussion and discussion not in selected and selected:selected[-1]=discussion
    official_indices=[i for i,item in enumerate(selected) if item['source_type']=='manufacturer / official'][:2]
    if official_indices:
        with ThreadPoolExecutor(max_workers=2) as pool:
            enriched=list(pool.map(lambda i:official_excerpt(selected[i],q),official_indices))
        for i,item in zip(official_indices,enriched):selected[i]=item
    for i,item in enumerate(selected):item['citation']=i+1
    successes=sum(ok for _,ok in responses)
    return {'status':('available' if successes==3 else 'partial') if selected else ('no_results' if successes else 'unavailable'),'results':selected}

class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        if not _official_url(newurl):return None
        return super().redirect_request(request,fp,code,msg,headers,newurl)

def _official_url(url):
    from ipaddress import ip_address
    import socket
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):return False
    if source_type(url)!='manufacturer / official':return False
    try:return all(ip_address(row[4][0]).is_global for row in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM))
    except (OSError,ValueError):return False

def official_excerpt(item,query):
    """Read bounded public official references only; never follow arbitrary links."""
    from html.parser import HTMLParser
    import io
    from backend.ai.maintenance_ai import knowledge_excerpt
    class Text(HTMLParser):
        def __init__(self):super().__init__();self.skip=0;self.parts=[]
        def handle_starttag(self,tag,attrs):
            if tag in ('script','style','nav'):self.skip+=1
        def handle_endtag(self,tag):
            if tag in ('script','style','nav'):self.skip=max(0,self.skip-1)
        def handle_data(self,data):
            if not self.skip:self.parts.append(data)
    try:
        if not _official_url(item['url']):return item
        opener=urllib.request.build_opener(_OfficialRedirect())
        with opener.open(urllib.request.Request(item['url'],headers={'User-Agent':'KurtexMaintenance/2.0'}),timeout=4) as response:
            raw=response.read(512_001);content_type=response.headers.get('Content-Type','')
        if len(raw)>512_000:return item
        if 'pdf' in content_type:
            from pypdf import PdfReader
            reader=PdfReader(io.BytesIO(raw));text='\n'.join(page.extract_text() or '' for page in reader.pages[:12])
        elif 'html' in content_type or 'text/plain' in content_type:
            parser=Text();parser.feed(raw.decode('utf-8','replace'));text=' '.join(parser.parts)
        else:return item
        excerpt=knowledge_excerpt(re.sub(r'\s+',' ',text),query,4500)
        if len(excerpt)>100:item={**item,'excerpt':excerpt,'evidence_level':'excerpt from fetched official page; applicability still requires verification'}
    except Exception:pass
    return item
