import time
from playwright.sync_api import sync_playwright
RAW=open('/tmp/prod_session').read().strip()
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
PIDS=["899038","733803","584301","923543","1150164","729124"]
FIX_CSS = """
#forecastLabel, #forecastNum, #forecastDelta, #forecastBaseline {display:none!important}
.panel:has(#forecastNum){display:none!important}
"""
def ctx(p, w, h, mobile=False):
    b=p.chromium.launch(executable_path=EXE, args=['--no-sandbox'])
    c=b.new_context(viewport={'width':w,'height':h}, device_scale_factor=2, is_mobile=mobile, has_touch=mobile, locale='ru-RU', timezone_id='Europe/Moscow')
    c.add_cookies([{'name':'pnl_session','value':RAW,'domain':'pnl.dodotool.ru','path':'/','secure':True,'httpOnly':True}])
    c.add_init_script("""
      localStorage.setItem('boardSort','revenue');
      localStorage.setItem('boardView','rich');
      localStorage.setItem('pnlSetupDismissed','1'); localStorage.setItem('svcHintDismissed','1');
      for (const u of ['andrey','default']) {
        localStorage.setItem('pnlDashboard.selectedProjects.'+u, JSON.stringify(%s));
        localStorage.setItem('pnlDashboard.periodMode.'+u, 'month');
      }
    """ % PIDS)
    return b,c
def fixname(pg):
    pg.evaluate("""()=>{document.querySelectorAll('*').forEach(e=>{ if(e.children.length===0 && /Andrey/.test(e.textContent)) e.textContent='★ Андрей'; });}""")
    pg.add_style_tag(content=FIX_CSS)

    pg.evaluate("""()=>{
      const K=0.87;
      const fmt=n=>Math.round(n).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g,'\\u00a0');
      const walk=(node)=>{ for(const ch of node.childNodes){ if(ch.nodeType===3){ ch.nodeValue=ch.nodeValue.replace(/(?<![\\d,.:])(\\d{1,3}(?:[\\u00a0 ]\\d{3}){1,3})(?![\\d,.:])/g,(m,g)=>{ const v=parseInt(g.replace(/[\\u00a0 ]/g,'')); return v>=10000?fmt(v*K):m; }); } else if(ch.nodeType===1 && ch.tagName!=='SCRIPT' && ch.tagName!=='STYLE' && ch.tagName!=='CANVAS') walk(ch); } };
      walk(document.body);
    }""")
def board(pg, name):
    pg.goto('https://pnl.dodotool.ru/board', wait_until='networkidle', timeout=120000); time.sleep(4)
    fixname(pg)
    # hide per-card forecast rows
    pg.evaluate("""()=>{document.querySelectorAll('.label').forEach(l=>{ if(/^Прогноз/.test(l.textContent)) { const row=l.closest('div'); if(row) row.style.display='none'; } });}""")
    pg.screenshot(path=name, full_page=True)
def fin(pg, name):
    pg.goto('https://pnl.dodotool.ru/', wait_until='networkidle', timeout=120000); time.sleep(2)
    pg.evaluate("()=>{const s=document.getElementById('monthSelect'); s.value='2026-08'; s.dispatchEvent(new Event('change',{bubbles:true}));}"); time.sleep(1)
    pg.evaluate("()=>{const t=document.getElementById('compareToggle'); if(t && !t.checked) t.click();}")
    pg.evaluate("()=>{document.querySelectorAll('input.js-grp-toggle').forEach(cb=>{ if(!cb.checked) cb.click(); }); document.querySelectorAll('input[data-pid]').forEach(cb=>{ if(!cb.checked) cb.click(); }); document.getElementById('projApplyBtn').click();}")
    time.sleep(5); pg.wait_for_load_state('networkidle', timeout=180000); time.sleep(30)
    pg.evaluate("()=>{document.body.classList.remove('drawer-open'); const bd=document.getElementById('drawerBackdrop'); if(bd) bd.hidden=true;}")
    fixname(pg)
    pg.screenshot(path=name, full_page=True)
import sys
job=sys.argv[1]
with sync_playwright() as p:
    if job=='bd': b,c=ctx(p,1440,900); pg=c.new_page(); board(pg,'board_desktop.png'); b.close()
    if job=='fd': b,c=ctx(p,1440,900); pg=c.new_page(); fin(pg,'fin_desktop.png'); b.close()
    if job=='bm': b,c=ctx(p,390,844,True); pg=c.new_page(); board(pg,'board_mobile.png'); b.close()
    if job=='fm': b,c=ctx(p,390,844,True); pg=c.new_page(); fin(pg,'fin_mobile.png'); b.close()
print('done',job)
