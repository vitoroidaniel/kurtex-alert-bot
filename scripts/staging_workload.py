"""Bounded staging workload through real Flask routes, no public auth bypass."""
import os, io, json, time, uuid, random, threading, statistics, shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from scripts.seed_staging import guard
from scripts.staging_presence import BASE, NAMES

def start(count=8, seconds=600):
    guard()
    if not 1 <= count <= 8 or not 30 <= seconds <= 1800:
        raise ValueError("Workload limit exceeded")
    def run():
        try:
            execute(count, seconds)
        except Exception:
            import traceback
            traceback.print_exc()
    threading.Thread(target=run,name="staging-workload",daemon=True).start()

def execute(count, seconds):
    guard()
    from api.server import app
    from backend.storage import db, smart_lists_store
    from backend.storage.case_store import workspace_revision
    from backend.storage.user_store import get_user
    from PIL import Image, ImageDraw
    prefix="stg-load-v1-"
    runid=uuid.uuid4().hex[:10]
    deadline=time.monotonic()+seconds
    metrics=defaultdict(list)
    errors=defaultdict(int)
    lock=threading.Lock()
    def call(client, token, label, method, path, **kwargs):
        t=time.monotonic()
        response=client.open(path,method=method,base_url="https://localhost",headers={"X-Workspace-CSRF":token},**kwargs)
        elapsed=(time.monotonic()-t)*1000
        data=response.get_json(silent=True) or {}
        with lock:
            metrics[label].append(elapsed)
            if not 200<=response.status_code<300:
                key=f"{label}:{response.status_code}:{str(data.get('error',''))[:100]}"
                errors[key]+=1
                if errors[key]==1: print("STAGING_WORKLOAD_ERROR "+key,flush=True)
        return data if 200<=response.status_code<300 else {}
    def client_for(uid):
        user=get_user(uid)
        if not user or not user.get("synthetic_staging"):
            raise RuntimeError("Not a synthetic user")
        client=app.test_client()
        token=uuid.uuid4().hex
        with client.session_transaction(base_url="https://localhost") as sess:
            sess["user"]={"id":uid,"first_name":user["name"],"role":"agent","username":""}
            sess["workspace_csrf"]=token
        return client,token
    lists=[]
    existing=smart_lists_store.list_all()
    for i in range(3):
        name=f"SIM Load Test {i+1}"
        hit=next((v for v in existing if v.get("name")==name),None)
        lists.append(hit["id"] if hit else smart_lists_store.create_list(name,[{"label":"Unit","type":"text"},{"label":"Repair status","type":"text"}],str(BASE)))
    def worker(i):
        uid=BASE+i
        client,token=client_for(uid)
        sid=str(uuid.uuid4())
        rng=random.Random(i+42)
        def req(label,method,path,**kwargs): return call(client,token,label,method,path,**kwargs)
        def presence(action,cid="",field="",draft=""):
            req("presence","POST","/api/workspace/presence",json=dict(session_id=sid,action=action,case_id=cid,field=field,draft=draft))
        def current():
            with db.read_conn() as conn:
                rows=conn.execute("SELECT data_json FROM cases WHERE id LIKE ? AND status='reported' AND closed_at IS NULL AND agent_id=? LIMIT 20",(prefix+"%",uid)).fetchall()
            records=[json.loads(r[0]) for r in rows]
            return rng.choice(records) if records else None
        try:
            for tick in range(90):
                if time.monotonic()>=deadline: break
                case=current()
                if case:
                    cid=case["id"]
                    if not cid.startswith(prefix) or case.get("synthetic_dataset")!="staging-load-v1": raise RuntimeError("Non-synthetic target")
                    path="/api/workspace/cases/"+cid
                    rev=workspace_revision(case)
                    action=tick%6
                    if action==0:
                        draft="SIM: Repair shop confirmed technician availability."
                        presence("editing",cid,"notes",draft[:17])
                        time.sleep(.5)
                        presence("editing",cid,"notes",draft)
                        req("note","PATCH",path,json=dict(action="note",revision=rev,text=draft))
                    elif action==1:
                        presence("dragging",cid)
                        req("rank","PATCH",path,json=dict(action="rank",board_rank=rng.randint(1,9999)))
                    elif action==2:
                        presence("editing",cid,"shop_info","SIM Repair Center")
                        req("edit","PATCH",path,json=dict(action="edit",revision=rev,fields={"shop_info":"SIM Repair Center","amount":str(rng.randint(150,1800))}))
                    elif action==3:
                        if shutil.disk_usage("/app/data").free>200*1024*1024:
                            im=Image.new("RGB",(640,360),"#dddddd")
                            ImageDraw.Draw(im).text((25,25),"SYNTHETIC REPAIR PHOTO "+runid,fill="black")
                            buf=io.BytesIO();im.save(buf,format="PNG");buf.seek(0)
                            req("attachment","POST",path+"/attachments",data={"file":(buf,"sim-repair.png"),"category":"attachment"})
                    elif action==4:
                        presence("dragging",cid)
                        req("move","PATCH",path,json=dict(action="move",revision=rev,agent_id=str(BASE+(i+1)%count),stage="",board_rank=rng.randint(1,9999),allow_concurrent_move=True))
                    else:
                        presence("viewing",cid)
                        req("stage","PATCH",path,json=dict(action="stage",revision=rev,stage="monitoring" if case.get("board_stage")!="monitoring" else "",allow_concurrent_move=True))
                if tick%3==0:
                    req("board","GET","/api/workspace/board")
                    req("history","GET","/api/cases?filter=all&limit=50")
                if tick%4==0:
                    lid=lists[i%len(lists)]
                    ent=req("list_add","POST",f"/api/smart-lists/{lid}/entries",json={"values":{"f0":f"SIM-{i}-{tick}","f1":"Repair pending"}})
                    req("list_read","GET",f"/api/smart-lists/{lid}")
                    if ent.get("id"):
                        req("list_edit","PUT",f"/api/smart-lists/{lid}/entries/{ent['id']}",json={"values":{"f0":f"SIM-{i}-{tick}","f1":"Repair complete"}})
                if tick%8==0:
                    chat=req("chat_create","POST","/api/ai/chats",json={"title":"SIM workload "+runid})
                    if chat.get("id"): req("chat_read","GET","/api/ai/chats/"+chat["id"])
                presence("idle")
                time.sleep(rng.uniform(2,5))
        finally:
            presence("left")
    print(f"STAGING_WORKLOAD started run={runid} agents={count} seconds={seconds}",flush=True)
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures=[pool.submit(worker,i) for i in range(count)]
        for f in futures:
            try: f.result()
            except Exception as ex: print("STAGING_WORKLOAD worker error "+str(ex),flush=True)
    with db.read_conn() as conn:
        integrity=conn.execute("PRAGMA quick_check").fetchone()[0]
    report={"run":runid,"agents":count,"integrity":integrity,"errors":dict(errors),"operations":{k:{"count":len(v),"p50_ms":round(statistics.median(v),1),"p95_ms":round(sorted(v)[min(len(v)-1,int(len(v)*.95))],1)} for k,v in metrics.items()}}
    print("STAGING_WORKLOAD_RESULT "+json.dumps(report),flush=True)
    with open("/app/data/staging-workload-"+runid+".json","w") as f: json.dump(report,f,indent=2)
