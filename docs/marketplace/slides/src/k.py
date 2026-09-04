import time, sys, json
from playwright.sync_api import sync_playwright
SID=open('/tmp/sa_sid').read().strip()
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
def ctx(p,w,h,mobile):
    b=p.chromium.launch(executable_path=EXE,args=['--no-sandbox'])
    c=b.new_context(viewport={'width':w,'height':h},device_scale_factor=2,is_mobile=mobile,has_touch=mobile,locale='ru-RU',timezone_id='Europe/Moscow')
    c.add_cookies([{'name':'dt_session','value':SID,'domain':'.dodotool.ru','path':'/','secure':True,'httpOnly':True}])
    return b,c
job=sys.argv[1]
with sync_playwright() as p:
    if job=='explore':
        b,c=ctx(p,390,844,True); pg=c.new_page()
        pg.goto('https://kassa.dodotool.ru/auth/sso?next=/',wait_until='networkidle',timeout=90000); time.sleep(3)
        print(pg.url); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:1500])
        print(json.dumps(pg.evaluate("[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')).filter((v,i,s)=>s.indexOf(v)===i).slice(0,40)"),ensure_ascii=False))
        pg.screenshot(path='k_home_m.png'); b.close()
    if job=='explore2':
        b,c=ctx(p,390,844,True); pg=c.new_page()
        pg.goto('https://kassa.dodotool.ru/auth/sso?next=/projects',wait_until='networkidle',timeout=90000); time.sleep(2)
        pg.get_by_text('Свод касс').click(); time.sleep(3); print('SVOD',pg.url); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:1200]); pg.screenshot(path='k_svod_m.png',full_page=True)
        pg.goto('https://kassa.dodotool.ru/projects',wait_until='networkidle'); time.sleep(2)
        pg.get_by_text('Алексин-1').click(); time.sleep(3); print('POINT',pg.url); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:2000]); pg.screenshot(path='k_point_m.png',full_page=True)
        print(json.dumps(pg.evaluate("[...document.querySelectorAll('button,a')].map(e=>e.innerText.trim()).filter(Boolean).slice(0,40)"),ensure_ascii=False))
        b.close()
    if job=='explore3':
        b,c=ctx(p,390,844,True); pg=c.new_page()
        pg.goto('https://kassa.dodotool.ru/auth/sso?next=/projects/10/journal',wait_until='networkidle',timeout=90000); time.sleep(3)
        pg.get_by_text('− Расход').click(); time.sleep(2); print('RASHOD',pg.url); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:1200]); pg.screenshot(path='k_rashod_m.png')
        pg.goto('https://kassa.dodotool.ru/projects/10/journal',wait_until='networkidle'); time.sleep(2)
        pg.click('header svg:last-of-type, [class*=settings], button[aria-label*="астро"]', timeout=5000) if False else None
        # click the sliders icon at top-right
        pg.mouse.click(360,70); time.sleep(2); print('MENU',pg.url); print(pg.evaluate("document.body.innerText").replace('\n',' | ')[:1500]); pg.screenshot(path='k_menu_m.png')
        b.close()
    if job=='final':
        b,c=ctx(p,390,844,True); pg=c.new_page()
        REN="()=>{const w=n=>{for(const ch of n.childNodes){ if(ch.nodeType===3) ch.nodeValue=ch.nodeValue.replace(/Коваль Андрей/g,'Иванова Мария').replace(/Закаримов Аслан/g,'Петров Иван').replace(/Зп Аслана[^A-Za-zА-Яа-я]*/g,'аванс за неделю'); else w(ch);}}; w(document.body); document.querySelectorAll('*').forEach(e=>{ if(e.children.length===0 && e.textContent.trim()==='КА') e.textContent='ИМ'; });}"
        pg.goto('https://kassa.dodotool.ru/auth/sso?next=/projects/10/journal',wait_until='networkidle',timeout=90000); time.sleep(3)
        pg.evaluate(REN); pg.screenshot(path='kassa_journal.png')
        pg.get_by_text('− Расход').click(); time.sleep(1.5)
        pg.click('input[inputmode], input[type=number], input[type=text]'); pg.keyboard.type('1250'); time.sleep(0.5)
        pg.click('input[placeholder*="Необязательно"]'); pg.keyboard.type('вода и салфетки, чек прилагаю'); time.sleep(0.5)
        pg.evaluate("()=>{document.activeElement && document.activeElement.blur()}"); time.sleep(0.5)
        pg.screenshot(path='kassa_expense.png')
        pg.goto('https://kassa.dodotool.ru/summary',wait_until='networkidle'); time.sleep(3); pg.evaluate(REN); pg.screenshot(path='kassa_summary.png')
        b.close()
    if job=='desk':
        b,c=ctx(p,1440,900,False); pg=c.new_page()
        REN="()=>{const w=n=>{for(const ch of n.childNodes){ if(ch.nodeType===3) ch.nodeValue=ch.nodeValue.replace(/Коваль Андрей/g,'Иванова Мария').replace(/Закаримов Аслан/g,'Петров Иван').replace(/Зп Аслана[^A-Za-zА-Яа-я]*/g,'аванс за неделю'); else w(ch);}}; w(document.body); document.querySelectorAll('*').forEach(e=>{ if(e.children.length===0 && e.textContent.trim()==='КА') e.textContent='ИМ'; });}"
        pg.goto('https://kassa.dodotool.ru/auth/sso?next=/projects/10/journal',wait_until='networkidle',timeout=90000); time.sleep(3)
        pg.evaluate(REN); pg.screenshot(path='kassa_journal_d.png')
        pg.goto('https://kassa.dodotool.ru/summary',wait_until='networkidle'); time.sleep(3); pg.evaluate(REN); pg.screenshot(path='kassa_summary_d.png')
        pg.goto('https://kassa.dodotool.ru/projects',wait_until='networkidle'); time.sleep(3); pg.evaluate(REN); pg.screenshot(path='kassa_projects_d.png')
        b.close()
