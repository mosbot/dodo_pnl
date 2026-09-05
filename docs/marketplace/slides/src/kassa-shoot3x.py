import time, sys, json
from playwright.sync_api import sync_playwright
SID=open('/tmp/sa_sid').read().strip()
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
REN="""()=>{const w=n=>{for(const ch of n.childNodes){ if(ch.nodeType===3) ch.nodeValue=ch.nodeValue.replace(/Коваль Андрей/g,'Иванов П.С.').replace(/Закаримов Аслан/g,'Петров И.А.').replace(/Зп Аслана[^A-Za-zА-Яа-я]*/g,'аванс за неделю').replace(/Алексин-1/g,'Кубинка-1').replace(/Долгих А\./g,'Смирнов Д.').replace(/Карнюшина Виктория/g,'Сидорова М.В.'); else w(ch);}}; w(document.body); document.querySelectorAll('*').forEach(e=>{ if(e.children.length===0 && e.textContent.trim()==='КА') e.textContent='ИП'; });}"""
def ctx(p):
    b=p.chromium.launch(executable_path=EXE,args=['--no-sandbox'])
    c=b.new_context(viewport={'width':1440,'height':900},device_scale_factor=3,locale='ru-RU',timezone_id='Europe/Moscow')
    c.add_cookies([{'name':'dt_session','value':SID,'domain':'.dodotool.ru','path':'/','secure':True,'httpOnly':True}])
    return b,c
job=sys.argv[1]
with sync_playwright() as p:
    b,c=ctx(p); pg=c.new_page()
    pg.goto('https://kassa.dodotool.ru/auth/sso?next=/projects/10/journal',wait_until='networkidle',timeout=90000); time.sleep(3); pg.evaluate(REN)
    if job=='journal': pg.screenshot(path='j3.png')
    if job=='close':
        pg.get_by_text('Закрыть смену').click(); time.sleep(2); ins=pg.locator('input[inputmode], input[type=text]:visible'); vals=['132310','4537']; k=0
        for i in range(ins.count()):
            el=ins.nth(i)
            try:
                if el.is_visible() and (el.get_attribute('placeholder') or '')=='0' or el.input_value()=='0':
                    el.click(); el.fill(vals[k]); k+=1
                    if k>=2: break
            except Exception as e: pass
        time.sleep(1); pg.evaluate(REN); pg.screenshot(path='close3.png'); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:900])
    if job=='couriers':
        pg.get_by_text('Курьеры:').click(); time.sleep(2.5); pg.evaluate(REN); pg.screenshot(path='cour3.png'); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:600])
    if job=='history':
        # click history icon on first row with it
        icons=pg.locator('table svg, tbody svg'); n=icons.count(); print('svgs',n)
        pg.locator('[title*="стори"], [aria-label*="стори"], button:has(svg)').first
        pg.evaluate("()=>{const rows=[...document.querySelectorAll('tr')]; for(const r of rows){ const svgs=[...r.querySelectorAll('svg')]; if(svgs.length>=2){ const t=svgs[svgs.length-1]; (t.closest('button')||t).dispatchEvent(new MouseEvent('click',{bubbles:true})); return; } } }"); time.sleep(2.5); pg.evaluate(REN); pg.screenshot(path='hist3.png'); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:500])
    b.close()
