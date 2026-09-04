import base64, sys
from playwright.sync_api import sync_playwright
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
def b64(p): return 'data:'+('font/ttf' if p.endswith('.ttf') else 'image/png')+';base64,'+base64.b64encode(open(p,'rb').read()).decode()
FONT=b64('InterVariable.ttf'); ICON=b64('icon02.png')
CSS="@font-face{font-family:Inter;src:url('"+FONT+"');font-weight:100 900}"+"""
*{box-sizing:border-box;margin:0;padding:0}
body{width:1280px;height:817px;overflow:hidden;font-family:Inter,sans-serif;color:#141a0e;background:#fff;position:relative}
.blob{position:absolute;border-radius:50%;filter:blur(70px);opacity:.85}
.b1{width:900px;height:900px;left:-380px;top:-420px;background:radial-gradient(circle at 40% 40%,#c9dea6 0%,#8fb34a 45%,rgba(143,179,74,0) 72%)}
.b2{width:760px;height:760px;right:-300px;bottom:-380px;background:radial-gradient(circle at 50% 50%,#b7d086 0%,#6f9433 45%,rgba(111,148,51,0) 72%)}
.b3{width:520px;height:520px;right:120px;top:-300px;background:radial-gradient(circle,#e3eecf 0%,rgba(227,238,207,0) 70%)}
.icon{position:absolute;right:52px;top:44px;width:76px;height:76px;border-radius:20px;box-shadow:0 8px 24px rgba(20,26,14,.18)}
h1{position:absolute;left:56px;top:64px;font-size:74px;line-height:1.06;font-weight:700;letter-spacing:-.025em;max-width:880px}
h1 span{color:#4a6b1a}
.pills{position:absolute;left:56px;right:56px;top:430px;display:grid;grid-template-columns:1fr 1fr;gap:20px}
.pill{background:rgba(255,255,255,.72);backdrop-filter:blur(8px);border:1px solid rgba(74,107,26,.18);border-radius:26px;padding:26px 30px;font-size:24px;line-height:1.3;font-weight:500;color:#1f2a12;box-shadow:0 10px 30px rgba(74,107,26,.08)}
.foot{position:absolute;left:56px;bottom:26px;font-size:16px;color:#5b6b48}
h2{position:absolute;left:56px;top:64px;font-size:40px;line-height:1.15;font-weight:700;letter-spacing:-.015em;max-width:1000px}
h2 span{color:#4a6b1a}
.frag{position:absolute;left:56px;right:56px;top:190px;bottom:0;display:flex;justify-content:center;align-items:flex-start}
.frag img{display:block;border-radius:18px 18px 0 0;box-shadow:0 26px 70px rgba(20,26,14,.22),0 0 0 1px rgba(20,26,14,.06);background:#fff}
"""
def page(body,out):
    html=f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='blob b1'></div><div class='blob b3'></div><div class='blob b2'></div><img class='icon' src='{ICON}'>{body}</body></html>"
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path=EXE,args=['--no-sandbox']); pg=b.new_page(viewport={'width':1280,'height':817},device_scale_factor=2); pg.set_content(html); pg.wait_for_timeout(400); pg.screenshot(path=out); b.close()
    print(out)
def feat(h,img,out,w=1168,top=190,shift=0):
    page(f"<h2>{h}</h2><div class='frag' style='top:{top}px'><img src='{b64(img)}' style='width:{w}px;margin-top:{shift}px'></div>",out)
which=sys.argv[1]
if which=='cover':
    page("<h1>Учёт наличных в пиццерии — <span>без Google-таблицы</span></h1>"
         "<div class='pills'><div class='pill'>Выручка по каналам подтягивается из Dodo IS</div><div class='pill'>Деньги у курьеров на руках — из заказов Dodo IS</div><div class='pill'>Закрытие смены со сверкой: «сошлось» или расхождение</div><div class='pill'>Чек к каждой операции и история правок</div></div>"
         "<div class='foot'>Для сетей пиццерий на Dodo IS · вход через Dodo IS · пробный период 14 дней</div>",'k2-0-cover.png')
if which=='close': feat('Закрытие смены со сверкой — <span>«сошлось» или расхождение</span>','frag-close.png','k2-1-close.png',w=760,top=170)
if which=='couriers': feat('Деньги у курьеров на руках — <span>из заказов Dodo IS</span>','frag-couriers.png','k2-2-couriers.png',w=1100,top=210)
if which=='journal': feat('Выручка из Dodo IS в журнале — <span>остатки по кассам и сейфу</span>','frag-journal.png','k2-3-journal.png',w=1168,top=190)
if which=='history': feat('История правок — <span>кто создал, кто и что изменил</span>','frag-history.png','k2-4-history.png',w=1000,top=210)
if which=='expense': feat('Чек к каждой операции — <span>файлом или фото с телефона</span>','frag-expense.png','k2-5-expense.png',w=640,top=170)
