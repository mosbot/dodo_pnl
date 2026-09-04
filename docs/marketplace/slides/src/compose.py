import base64, sys
from playwright.sync_api import sync_playwright
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
def b64(p): return 'data:'+('image/svg+xml' if p.endswith('.svg') else 'font/ttf' if p.endswith('.ttf') else 'image/png')+';base64,'+base64.b64encode(open(p,'rb').read()).decode()
CSS = "@font-face{font-family:Inter;src:url('"+b64('InterVariable.ttf')+"');font-weight:100 900}" + """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1600px;height:900px;overflow:hidden;font-family:Inter,'DejaVu Sans',sans-serif;color:#0f172a;
 background:linear-gradient(135deg,#eef3ff 0%,#f8fafc 55%,#e6f0ff 100%);position:relative}
.brand{position:absolute;left:64px;top:52px;display:flex;align-items:center;gap:14px;white-space:nowrap;font-weight:700;font-size:26px;color:#0f172a}
.brand img{width:40px;height:40px}
.brand b{color:#2563eb;font-weight:700;display:inline}
.tag{position:absolute;right:64px;top:56px;font-size:20px;color:#475569;letter-spacing:.04em;text-transform:uppercase;font-weight:700}
h1{position:absolute;left:64px;top:120px;font-size:52px;line-height:1.12;font-weight:700;letter-spacing:-.015em;max-width:1400px}
h1 span{color:#2563eb}
p.sub{position:absolute;left:64px;top:262px;font-size:26px;line-height:1.35;color:#475569;max-width:1100px}
.shot{position:absolute;left:64px;right:64px;top:352px;height:620px;border-radius:18px 18px 0 0;overflow:hidden;
 box-shadow:0 30px 80px rgba(15,23,42,.18),0 0 0 1px rgba(15,23,42,.06);background:#fff}
.shot .bar{height:36px;background:#f1f5f9;display:flex;align-items:center;gap:8px;padding-left:16px;border-bottom:1px solid #e2e8f0}
.shot .bar i{width:12px;height:12px;border-radius:50%;background:#cbd5e1;display:block}
.shot img{display:block;width:100%}
.phones{position:absolute;left:0;right:0;top:340px;display:flex;justify-content:center;gap:70px}
.phones.three{gap:44px;top:352px}
.phone{width:340px;height:640px;flex:none;border-radius:44px;background:#0f172a;padding:12px;box-shadow:0 30px 80px rgba(15,23,42,.25)}
.phone .scr{width:100%;height:100%;border-radius:34px;overflow:hidden;background:#fff}
.phone img{width:100%;display:block}
.tiles{position:absolute;left:64px;right:64px;top:400px;display:flex;gap:28px}
.tile{flex:1;background:#fff;border-radius:20px;padding:34px 34px 30px;box-shadow:0 20px 60px rgba(15,23,42,.10),0 0 0 1px rgba(15,23,42,.05)}
.tile .ic{width:56px;height:56px;border-radius:14px;display:flex;align-items:center;justify-content:center;margin-bottom:22px;font-size:28px;color:#fff;font-weight:700}
.tile h3{font-size:32px;margin-bottom:12px;font-weight:700}
.tile p{font-size:21px;line-height:1.35;color:#475569}
.foot{position:absolute;left:64px;bottom:44px;font-size:20px;color:#64748b}
"""
def page(body, out):
    html=f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path=EXE,args=['--no-sandbox'])
        pg=b.new_page(viewport={'width':1600,'height':900},device_scale_factor=2)
        pg.set_content(html); pg.wait_for_timeout(300); pg.screenshot(path=out); b.close()
    print(out)
LOGO=b64('icon.svg')
def brand(tag): return f"<div class='brand'><img src='{LOGO}'><span style='display:inline'>Dodo<b style='margin:0'>tool</b></span></div><div class='tag'>{tag}</div>"
def desk(tag,h,sub,img,out,top=0):
    page(brand(tag)+f"<h1>{h}</h1><p class='sub'>{sub}</p><div class='shot'><div class='bar'><i></i><i></i><i></i></div><img src='{b64(img)}' style='margin-top:{top}px'></div>",out)

which=sys.argv[1]
if which=='0':
    page(brand('для сетей пиццерий в Dodo IS')+"<h1>Касса, Финансы и Пульс сети — <span>из Dodo IS, без таблиц</span></h1><p class='sub'>Три инструмента, которые закрывают ежедневную рутину владельца и управляющих. Данные подтягиваются из Dodo IS автоматически.</p>"
    "<div class='tiles'>"
    "<div class='tile'><div class='ic' style='background:#0ea5e9'>₽</div><h3>Касса</h3><p>Кассовый журнал точки: смена, сейф, инкассации, деньги курьеров — с телефона.</p></div>"
    "<div class='tile'><div class='ic' style='background:#2563eb'>P&L</div><h3>Финансы</h3><p>Прибыль по каждой пиццерии, метрики Dodo IS, цели и цветовые зоны.</p></div>"
    "<div class='tile'><div class='ic' style='background:#f59e0b'>⚡</div><h3>Пульс</h3><p>Вся сеть на одном экране: выручка против прошлой недели, стопы, кухня, доставка.</p></div>"
    "</div><div class='foot'>Работает в сетях от 1 до 100 пиццерий · вход через Dodo IS · пробный период 14 дней</div>",'slide-0-cover.png')
if which=='1':
    desk('Пульс','Вся сеть на одном экране — <span>прямо сейчас</span>','Выручка каждой пиццерии против такого же дня прошлой недели, месяц LFL, активные стопы, скорость кухни и доставки. За 30 секунд понятно, куда звонить.','board.png','slide-1-pulse.png')
if which=='2':
    desk('Финансы','Прибыль по каждой пиццерии — <span>с целями и зонами</span>','Выручка по каналам, маржинальная и чистая прибыль, UC/KC/DC/LC, метрики Dodo IS и рейтинги — одна карточка месяца на точку. Красное видно сразу.','fin_cards.png','slide-2-finance-cards.png',top=-150)
if which=='3':
    desk('Финансы','P&L сети по статьям — <span>без бухгалтера и таблиц</span>','Детализация по пиццериям и по сети: выручка, переменные и постоянные расходы, маржинальная прибыль, EBITDA. Закрытые месяцы хранятся, текущий обновляется ежедневно.','fin_pnl.png','slide-3-finance-pnl.png')
if which=='4':
    page(brand('Пульс · Финансы · Касса')+"<h1>Работает с телефона — <span>ставить ничего не надо</span></h1><p class='sub'>Владелец смотрит сеть по дороге, управляющий закрывает смену на точке. Один вход через Dodo IS.</p>"
    f"<div class='phones'><div class='phone'><div class='scr'><img src='{b64('board_m.png')}'></div></div><div class='phone'><div class='scr'><img src='{b64('fin_m.png')}'></div></div></div>",'slide-4-mobile.png')

if which=='5':
    page(brand('Касса')+"<h1>Смена закрыта за две минуты — <span>деньги сошлись</span></h1><p class='sub'>Кассы, сейф и деньги курьеров по каждой точке; расход в три касания с фото чека; свод по сети у владельца.</p>"
    f"<div class='phones three'><div class='phone'><div class='scr'><img src='{b64('kassa_journal.png')}'></div></div><div class='phone'><div class='scr'><img src='{b64('kassa_expense.png')}'></div></div><div class='phone'><div class='scr'><img src='{b64('kassa_summary.png')}'></div></div></div>",'slide-5-kassa.png')

if which=='5d':
    desk('Касса','Кассовый журнал вместо таблицы — <span>выручка и курьеры из Dodo IS</span>','Выручка и деньги на руках у курьеров подтягиваются из Dodo IS сами; вручную только расходы — с фото чека. Каждая операция в истории: кто, когда, что изменил.','kassa_desk.png','slide-5-kassa.png',top=-20)

if which=='k1':
    desk('Касса','Учёт наличных вместо Google-таблицы — <span>выручка и курьеры из Dodo IS</span>','Выручка по каналам и заказы курьеров подтягиваются из Dodo IS и попадают в журнал отдельными операциями. Вручную — только расходы, к каждому чек или накладная.','k1.png','slide-5-kassa.png',top=0)
if which=='k2':
    desk('Касса','Закрытие смены со сверкой — <span>«сошлось» или расхождение</span>','Факт по каждой кассе сверяется с расчётным остатком; Касса предупредит, если у курьера не сданы деньги. Передача смены под подпись.','k2.png','slide-6-kassa-close.png',top=-230)

if which=='kassa':
    desk('Касса','Учёт наличных вместо Google-таблицы — <span>выручка из Dodo IS</span>','Выручка по каналам подтягивается из Dodo IS и попадает в журнал отдельными операциями. Остатки по кассам и сейфу, операции за день — в одном окне.','k1.png','kassa-1-journal.png',top=0)
    desk('Касса','Закрытие смены со сверкой — <span>«сошлось» или расхождение</span>','Факт по каждой кассе сверяется с расчётным остатком; Касса предупредит, если у курьера не сданы деньги. Передача смены под подпись.','k2.png','kassa-2-shift-close.png',top=-230)
    desk('Касса','Деньги у курьеров на руках — <span>из Dodo IS</span>','Выдали сдачу, приняли выручку — по каждому курьеру видно, сколько у него на руках. Несданная сумма остаётся на курьере до следующей смены.','k4.png','kassa-3-couriers.png',top=0)
    desk('Касса','Чек к каждой операции — <span>файлом или фото с телефона</span>','К любому приходу или расходу прикрепляется чек или накладная (jpg, png, pdf). Статьи расходов единые для сети, задаёт владелец.','k5.png','kassa-4-expense.png',top=-200)
    desk('Касса','История правок — <span>кто создал, кто и что изменил</span>','У каждой операции видно автора, изменения и время. Удаление тоже остаётся в истории с причиной — спорить о пересменке не о чем.','k7.png','kassa-5-history.png',top=-200)
