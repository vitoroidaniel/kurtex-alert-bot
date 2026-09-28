"""Automatic fleet-experience index built from resolved/reported case records.

Fleet evidence is retrieval context, not manually approved repair guidance.  It is
kept separate from developer-reviewed AI feedback/lessons.
"""
import hashlib, json, sqlite3
from datetime import datetime, timezone
from backend.ai.maintenance_ai import tokens, knowledge_excerpt

USEFUL_FIELDS=(
 'id','vehicle_type','unit_number','report_driver','driver_name','group_name','issue_text',
 'description','notes','status','resolution','solution','close_notes','closing_notes',
 'resolution_notes','comments','load_type','location','priority','pickup','delivery',
 'setpoint','current_temp','temp_recorder','report_text','report_data','media','opened_at',
 'assigned_at','closed_at','agent_name','closed_by_name','resolution_secs','response_secs'
)

class FleetKnowledgeStore:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS fleet_cases (
              case_id TEXT PRIMARY KEY, fingerprint TEXT, status TEXT, searchable TEXT,
              payload TEXT, quality TEXT, updated_at TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS fleet_cases_status ON fleet_cases(status,updated_at)')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db
    def _record(self,case):
        data={k:case.get(k) for k in USEFUL_FIELDS if case.get(k) not in (None,'',[],{})}
        # Preserve additional scalar/report fields so future report questions are searchable.
        for k,v in case.items():
            if k not in data and isinstance(v,(str,int,float,bool)) and v not in ('',None):data[k]=v
        return data
    def ingest_cases(self,cases):
        changed=0; skipped=0; now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            for case in cases:
                cid=str(case.get('id') or case.get('full_id') or '').strip()
                if not cid:skipped+=1;continue
                data=self._record(case); payload=json.dumps(data,ensure_ascii=False,sort_keys=True,default=str)
                fp=hashlib.sha256(payload.encode()).hexdigest()
                # A case can be useful even before closure if a detailed report exists.  Mark
                # incomplete evidence accordingly so the AI never treats it as a confirmed fix.
                resolved=bool(case.get('closed_at') or str(case.get('status','')).lower()=='done')
                detail=' '.join(str(data.get(k,'')) for k in ('issue_text','description','notes','comments','resolution','solution','close_notes','resolution_notes'))
                quality='resolved' if resolved else ('reported' if len(detail.strip())>=20 else 'incomplete')
                old=db.execute('SELECT fingerprint FROM fleet_cases WHERE case_id=?',(cid,)).fetchone()
                if old and old['fingerprint']==fp:continue
                searchable=' '.join(str(v) for v in data.values() if isinstance(v,(str,int,float,bool)))
                db.execute('''INSERT INTO fleet_cases(case_id,fingerprint,status,searchable,payload,quality,updated_at)
                  VALUES(?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                  status=excluded.status,searchable=excluded.searchable,payload=excluded.payload,
                  quality=excluded.quality,updated_at=excluded.updated_at''',
                  (cid,fp,str(case.get('status') or ''),searchable,payload,quality,now));changed+=1
        return {'changed':changed,'skipped':skipped}
    def stats(self):
        with self.connect() as db:
            total=db.execute('SELECT count(*) FROM fleet_cases').fetchone()[0]
            q={r[0]:r[1] for r in db.execute('SELECT quality,count(*) FROM fleet_cases GROUP BY quality')}
            last=db.execute('SELECT max(updated_at) FROM fleet_cases').fetchone()[0]
        return {'total':total,'resolved':q.get('resolved',0),'reported':q.get('reported',0),'incomplete':q.get('incomplete',0),'last_sync':last}
    def matches(self,query,limit=6):
        wanted=tokens(query)
        if not wanted:return []
        with self.connect() as db: rows=db.execute('SELECT case_id,payload,quality,searchable FROM fleet_cases').fetchall()
        ranked=[]
        for row in rows:
            score=len(wanted & tokens(row['searchable']))
            if not score:continue
            data=json.loads(row['payload']); data['case_id']=row['case_id'];data['evidence_quality']=row['quality']
            # keep prompt payload bounded
            for k,v in list(data.items()):
                if isinstance(v,str) and len(v)>1800:data[k]=knowledge_excerpt(v,query,1800)
            ranked.append((score,1 if row['quality']=='resolved' else 0,data))
        return [x[2] for x in sorted(ranked,key=lambda x:(-x[0],-x[1]))[:limit]]
