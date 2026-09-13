"""Сбор дневной выручки по заведениям за 2025-01..2026-09 для бэктеста прогноза.
Ручка /finances/sales/units/daily: окно ≤10 дней, батч ≤30 юнитов."""
import httpx, sys, json, time, calendar
sys.path.insert(0,'/app')
from app.database import SessionLocal
from app.crud import dodois_credentials as cc

SUBS={"1":"000d3a21","3":"5221ac3e"}
units={"1":[], "3":[]}
for line in open('/tmp/units13.txt'):
    line=line.strip()
    if not line: continue
    k,uuid,name=line.split('|')
    units[k].append((uuid.strip(),name.strip()))

def decades(y,m):
    last=calendar.monthrange(y,m)[1]
    return [(f"{y}-{m:02d}-01",f"{y}-{m:02d}-10"),
            (f"{y}-{m:02d}-11",f"{y}-{m:02d}-20"),
            (f"{y}-{m:02d}-21",f"{y}-{m:02d}-{last:02d}")]

months=[(2025,m) for m in range(1,13)]+[(2026,m) for m in range(1,10)]
out={}
with SessionLocal() as db:
    creds={k:[x for x in cc.list_all_credentials(db) if x.sub.startswith(p)][0].access_token
           for k,p in SUBS.items()}
total=0
for key,uu in units.items():
    tok=creds[key]; data={}
    batches=[uu[i:i+30] for i in range(0,len(uu),30)]
    with httpx.Client(timeout=90,headers={"Authorization":"Bearer "+tok}) as h:
        for (y,m) in months:
            for frm,to in decades(y,m):
                for b in batches:
                    ids=",".join(u for u,_ in b)
                    for attempt in range(3):
                        try:
                            r=h.get("https://api.dodois.io/dodopizza/ru/finances/sales/units/daily",
                                    params={"units":ids,"fromDate":frm,"toDate":to})
                            if r.status_code==200: break
                            if r.status_code==429: time.sleep(8); continue
                            print("ERR",r.status_code,frm,to,r.text[:80],flush=True); r=None; break
                        except Exception as e:
                            print("EXC",type(e).__name__,frm,to,flush=True); time.sleep(3); r=None
                    if not r or r.status_code!=200: continue
                    for row in r.json().get("result",[]):
                        data.setdefault(row["unitId"],{})[row["date"]]=float(row.get("sales") or 0)
                    total+=1
                    time.sleep(0.2)
            print(f"key{key} {y}-{m:02d} готово, запросов {total}",flush=True)
    out[key]={"units":{u:n for u,n in units[key]},"daily":data}
json.dump(out,open('/tmp/daily_raw.json','w'))
print("DONE. запросов:",total,"| юнитов с данными:",{k:len(v["daily"]) for k,v in out.items()},flush=True)
