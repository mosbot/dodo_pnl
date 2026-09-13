"""Бэктест: weekday-профиль (S24) против календарного LFL на закрытых месяцах 2026."""
import json, calendar, statistics
from datetime import date

D=json.load(open('/tmp/daily_raw.json'))
NAMES={"1":"PiX","3":"XFood"}
HOLIDAY={1,5}  # январь, май — праздники привязаны к датам

def fc_weekday(mtd, ly_days, month_first, dim, done):
    if mtd<=0 or not ly_days or done<=0: return None
    by={}; cnt={}
    for ds,v in ly_days.items():
        dow=date.fromisoformat(ds[:10]).weekday()
        by[dow]=by.get(dow,0.0)+float(v or 0); cnt[dow]=cnt.get(dow,0)+1
    if not by: return None
    avg=sum(by.values())/max(sum(cnt.values()),1)
    w={d:by[d]/cnt[d] for d in by}
    weights=[w.get(date(month_first.year,month_first.month,i).weekday(),avg) for i in range(1,dim+1)]
    tot=sum(weights); pas=sum(weights[:min(done,dim)])
    return mtd*tot/pas if tot>0 and pas>0 else None

def fc_calendar(mtd, mtd_lfl, ly_full, dim, done):
    if mtd<=0: return None,"none"
    if mtd_lfl>0 and ly_full>0: return mtd*ly_full/mtd_lfl,"lfl"
    if done>0: return mtd*dim/done,"pace"
    return None,"none"

def msum(daily, y, m, d1=1, d2=31):
    s=0.0
    for ds,v in daily.items():
        if ds.startswith(f"{y}-{m:02d}") and d1<=int(ds[8:10])<=d2: s+=float(v or 0)
    return s

rows=[]   # (key, month, cut, scope, method, err)
for key,blob in D.items():
    daily=blob["daily"]; names=blob["units"]
    # сетевой профиль LY по месяцам (для точек без своего прошлого года)
    for m in range(1,9):
        dim=calendar.monthrange(2026,m)[1]
        net_ly={}
        for u,dd in daily.items():
            for ds,v in dd.items():
                if ds.startswith(f"2025-{m:02d}"): net_ly[ds]=net_ly.get(ds,0.0)+float(v or 0)
        for cut in (6,12,20):
            net={"fact":0.0,"wd":0.0,"cal":0.0}
            for u,dd in daily.items():
                fact=msum(dd,2026,m)
                mtd=msum(dd,2026,m,1,cut)
                if fact<=0 or mtd<=0: continue
                days_with=sum(1 for ds,v in dd.items() if ds.startswith(f"2026-{m:02d}") and (v or 0)>0)
                if days_with < dim-3: continue           # открылась/закрылась посреди месяца
                ly_days={ds:v for ds,v in dd.items() if ds.startswith(f"2025-{m:02d}")}
                ly_full=sum(ly_days.values()); mtd_lfl=msum(dd,2025,m,1,cut)
                own=bool(ly_days) and ly_full>0
                wd=fc_weekday(mtd, ly_days if own else net_ly, date(2026,m,1), dim, cut)
                cal,cm=fc_calendar(mtd, mtd_lfl, ly_full, dim, cut)
                if wd: rows.append((key,m,cut,"unit","weekday" if own else "weekday_net",(wd-fact)/fact))
                if cal: rows.append((key,m,cut,"unit",cm,(cal-fact)/fact))
                net["fact"]+=fact; net["wd"]+=wd or 0; net["cal"]+=cal or 0
            if net["fact"]>0:
                rows.append((key,m,cut,"net","weekday",(net["wd"]-net["fact"])/net["fact"]))
                rows.append((key,m,cut,"net","calendar",(net["cal"]-net["fact"])/net["fact"]))

def mape(sel): 
    v=[abs(e) for e in sel]
    return (statistics.mean(v)*100) if v else float('nan')
def bias(sel):
    return (statistics.mean(sel)*100) if sel else float('nan')

print("=== УРОВЕНЬ СЕТИ: ошибка прогноза месяца, % (знак = смещение) ===")
for key in sorted(D):
    print(f"\n{NAMES[key]}:")
    print(f"{'мес':>4} | {'после 6 дней':>22} | {'после 12 дней':>22} | {'после 20 дней':>22}")
    print(f"{'':>4} | {'weekday   календарь':>22} | {'weekday   календарь':>22} | {'weekday   календарь':>22}")
    for m in range(1,9):
        cells=[]
        for cut in (6,12,20):
            w=[e for k,mm,c,s,me,e in rows if k==key and mm==m and c==cut and s=="net" and me=="weekday"]
            c_=[e for k,mm,c,s,me,e in rows if k==key and mm==m and c==cut and s=="net" and me=="calendar"]
            cells.append(f"{(w[0]*100 if w else float('nan')):+7.1f}  {(c_[0]*100 if c_ else float('nan')):+8.1f}")
        mark=" ←праздники" if m in HOLIDAY else ""
        print(f"{m:>4} | {cells[0]:>22} | {cells[1]:>22} | {cells[2]:>22}{mark}")

print("\n=== ИТОГО (MAPE по сети, все месяцы) ===")
for key in sorted(D):
    for cut in (6,12,20):
        w=[e for k,mm,c,s,me,e in rows if k==key and c==cut and s=="net" and me=="weekday"]
        c_=[e for k,mm,c,s,me,e in rows if k==key and c==cut and s=="net" and me=="calendar"]
        print(f"{NAMES[key]:6} после {cut:>2} дней:  weekday {mape(w):5.2f}%  (смещение {bias(w):+5.2f}%)   календарь {mape(c_):5.2f}%  (смещение {bias(c_):+5.2f}%)")

print("\n=== ПРАЗДНИЧНЫЕ vs ОБЫЧНЫЕ месяцы (сеть, MAPE) ===")
for label,ms in (("январь+май",[1,5]),("остальные",[2,3,4,6,7,8])):
    for cut in (6,12,20):
        w=[e for k,mm,c,s,me,e in rows if mm in ms and c==cut and s=="net" and me=="weekday"]
        c_=[e for k,mm,c,s,me,e in rows if mm in ms and c==cut and s=="net" and me=="calendar"]
        print(f"{label:11} после {cut:>2} дней:  weekday {mape(w):5.2f}%   календарь {mape(c_):5.2f}%")

print("\n=== ПО ЗАВЕДЕНИЯМ (MAPE, все месяцы и точки) ===")
for cut in (6,12,20):
    for me in ("weekday","lfl","weekday_net","pace"):
        v=[e for k,mm,c,s,m2,e in rows if c==cut and s=="unit" and m2==me]
        if v: print(f"после {cut:>2} дней  {me:12} n={len(v):>4}  MAPE {mape(v):5.2f}%  смещение {bias(v):+6.2f}%")
    print()
