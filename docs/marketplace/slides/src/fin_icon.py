from playwright.sync_api import sync_playwright
EXE='/sessions/dreamy-wonderful-gates/.cache/ms-playwright/chromium-1234/chrome-linux/chrome'
PATHS=('<path d="M3 13a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>'
 '<path d="M15 9a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"/>'
 '<path d="M9 5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1z"/><path d="M4 20h14"/>')
def svg(size,stroke,bg,fg,pad):
    g=size-2*pad
    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 {size} {size}'>
<rect width='{size}' height='{size}' fill='{bg}'/>
<g transform='translate({pad} {pad}) scale({g/24})' fill='none' stroke='{fg}' stroke-width='{stroke}' stroke-linecap='round' stroke-linejoin='round'>{PATHS}</g></svg>"""
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=EXE,args=['--no-sandbox'])
    for name,s in [('fin-icon-1024.png',svg(1024,1.9,'#2563eb','#fff',250)),('fin-glyph.png',svg(535,1.9,'transparent','#2563eb',20))]:
        pg=b.new_page(viewport={'width':1024,'height':1024}); pg.set_content(f"<body style='margin:0'>{s}</body>")
        pg.locator('svg').screenshot(path=name,omit_background=True); pg.close()
    b.close()
# solid light-blue bg for logo background
from PIL import Image
Image.new('RGB',(1060,1060),(219,231,254)).save('fin-logo-bg.png')
print('ok')
