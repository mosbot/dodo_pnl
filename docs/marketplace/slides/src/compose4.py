import base64, sys
from playwright.sync_api import sync_playwright
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
def b64(p): return 'data:'+('font/ttf' if p.endswith('.ttf') else 'image/png')+';base64,'+base64.b64encode(open(p,'rb').read()).decode()
FONT=b64('InterVariable.ttf'); GLYPH=b64('fin-glyph.png')
CSS="@font-face{font-family:Inter;src:url('"+FONT+"');font-weight:100 900}"+"""
*{box-sizing:border-box;margin:0;padding:0}
body{width:1280px;height:817px;overflow:hidden;font-family:Inter,sans-serif;color:#0f172a;background:#f4f6fb;position:relative}
.band{position:absolute;left:0;top:0;right:0;height:350px;background:#1d4ed8}
.band.cover{height:817px;background:linear-gradient(180deg,#1d4ed8 0%,#1e3a8a 100%)}
.brand{position:absolute;left:56px;top:44px;display:flex;align-items:center;gap:14px;color:#fff;font-weight:600;font-size:22px;letter-spacing:.01em}
.brand .g{width:44px;height:44px;border-radius:12px;background:#fff;display:flex;align-items:center;justify-content:center}
.brand .g img{width:30px}
.brand span b{font-weight:800}
.tag{position:absolute;right:56px;top:52px;color:rgba(255,255,255,.75);font-size:18px;letter-spacing:.08em;text-transform:uppercase;font-weight:600}
h1{position:absolute;left:56px;top:124px;color:#fff;font-size:66px;line-height:1.06;font-weight:700;letter-spacing:-.025em;max-width:1000px}
h1 em{font-style:normal;color:#bfdbfe}
.checks{position:absolute;left:56px;top:330px;display:grid;grid-template-columns:1fr 1fr;gap:22px 48px;width:1168px}
.chk{display:flex;gap:16px;align-items:flex-start;color:#fff;font-size:25px;line-height:1.3;font-weight:500}
.chk i{flex:none;width:34px;height:34px;border-radius:50%;background:#bfdbfe;color:#1d4ed8;display:flex;align-items:center;justify-content:center;font-style:normal;font-weight:800;font-size:20px;margin-top:2px}
.foot{position:absolute;left:56px;top:500px;color:rgba(255,255,255,.7);font-size:17px}
.peek{position:absolute;left:56px;right:56px;top:545px;height:272px;overflow:hidden}
.peek img{width:1168px;display:block;border-radius:16px 16px 0 0;box-shadow:0 24px 60px rgba(0,0,0,.3)}
h2{position:absolute;left:56px;top:108px;color:#fff;font-size:44px;line-height:1.15;font-weight:700;letter-spacing:-.015em;max-width:1100px}
h2 em{font-style:normal;color:#bfdbfe}
.sub{position:absolute;left:56px;top:232px;color:rgba(255,255,255,.82);font-size:21px;line-height:1.35;max-width:1000px}
.frag{position:absolute;left:56px;right:56px;top:300px;bottom:0;display:flex;justify-content:center;align-items:flex-start}
.frag img{display:block;border-radius:16px 16px 0 0;box-shadow:0 24px 60px rgba(15,23,42,.22),0 0 0 1px rgba(15,23,42,.06);background:#fff}
"""
def page(body,out,cover=False):
    html=f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='band{' cover' if cover else ''}'></div><div class='brand'><div class='g'><img src='{GLYPH}'></div><span>Dodo<b>tool</b> Финансы</span></div>{body}</body></html>"
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path=EXE,args=['--no-sandbox']); pg=b.new_page(viewport={'width':1280,'height':817},device_scale_factor=2); pg.set_content(html); pg.wait_for_timeout(400); pg.screenshot(path=out); b.close()
    print(out)
def feat(h,sub,img,out,w=1168,top=322):
    page(f"<h2>{h}</h2><div class='sub'>{sub}</div><div class='frag' style='top:{top}px'><img src='{b64(img)}' style='width:{w}px'></div>",out)
which=sys.argv[1]
F='../fin/'
if which in('cover','all'):
    page("<h1>Прибыль по каждой пиццерии — <em>без ожидания отчёта</em></h1>"
         "<div class='checks'><div class='chk'><i>✓</i>Выручка, UC/KC/DC/LC, EBITDA и чистая прибыль по точке и по сети</div><div class='chk'><i>✓</i>Цели и цветовые зоны — сразу видно, где «в красном»</div><div class='chk'><i>✓</i>Метрики Dodo IS и рейтинги РКО, РС, клиентов рядом с деньгами</div><div class='chk'><i>✓</i>Пульс: вся сеть сегодня против прошлой недели</div></div>"
         "<div class='foot'>Для сетей пиццерий на Dodo IS · вход через Dodo IS · пробный период 14 дней</div><div class='peek'><img src='"+b64(F+'frag-cards.png')+"'></div>",'../fin/f-0-cover.png',cover=True)
if which in('cards','all'): feat('Карточка месяца по каждой точке — <em>с целями и зонами</em>','Выручка по каналам, маржинальная и чистая прибыль, EBITDA, метрики P&L и Dodo IS. Красное, жёлтое, зелёное — против цели и прошлого года.',F+'frag-cards.png','../fin/f-1-cards.png',w=1168,top=322)
if which in('pnl','all'): feat('P&L сети по статьям — <em>как в управленческом отчёте</em>','Выручка, переменные и постоянные расходы, маржинальная прибыль по каждой пиццерии и по сети. Раскрытие до статьи, выгрузка в xlsx.',F+'frag-pnl.png','../fin/f-2-pnl.png',w=1168,top=322)
if which in('yoy','all'): feat('Год к году — <em>по месяцам и по каналам</em>','Закрытые месяцы хранятся, текущий обновляется ежедневно. Доставка, ресторан, самовывоз — против того же месяца прошлого года.',F+'frag-charts-yoy.png','../fin/f-3-yoy.png',w=1168,top=322)
if which in('pulse','all'): feat('Пульс: вся сеть сегодня — <em>против прошлой недели</em>','Выручка каждой пиццерии, активные стопы, скорость кухни и доставки, месяц LFL. За 30 секунд понятно, куда звонить.',F+'frag-board.png','../fin/f-4-pulse.png',w=1168,top=322)
