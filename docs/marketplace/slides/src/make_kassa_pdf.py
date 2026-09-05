from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.lib.styles import ParagraphStyle
F="/usr/share/fonts/truetype/lato/"
pdfmetrics.registerFont(TTFont("Lato",F+"Lato-Regular.ttf")); pdfmetrics.registerFont(TTFont("Lato-Bold",F+"Lato-Bold.ttf")); pdfmetrics.registerFont(TTFont("Lato-Light",F+"Lato-Light.ttf"))
pdfmetrics.registerFont(TTFont("DJ","/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")); RUB='<font name="DJ">₽</font>'
pdfmetrics.registerFontFamily("Lato",normal="Lato",bold="Lato-Bold",italic="Lato",boldItalic="Lato-Bold")
OLIVE=colors.HexColor("#4a6b1a"); INK=colors.HexColor("#141a0e"); MUTED=colors.HexColor("#6b7280"); LINE=colors.HexColor("#e5e7eb"); SOFT=colors.HexColor("#eef3e6"); HEAD=colors.HexColor("#f3f6fb")
st={"h1":ParagraphStyle("h1",fontName="Lato-Bold",fontSize=22,leading=27,textColor=INK,spaceAfter=2*mm),
"sub":ParagraphStyle("sub",fontName="Lato-Light",fontSize=11.5,leading=15,textColor=MUTED,spaceAfter=7*mm),
"h2":ParagraphStyle("h2",fontName="Lato-Bold",fontSize=13.5,leading=17,textColor=INK,spaceBefore=6*mm,spaceAfter=3*mm),
"p":ParagraphStyle("p",fontName="Lato",fontSize=10,leading=14,textColor=INK,spaceAfter=2.5*mm),
"small":ParagraphStyle("small",fontName="Lato",fontSize=8.5,leading=11.5,textColor=MUTED),
"cell":ParagraphStyle("cell",fontName="Lato",fontSize=9.5,leading=12,textColor=INK),
"cellb":ParagraphStyle("cellb",fontName="Lato-Bold",fontSize=9.5,leading=12,textColor=INK),
"cellm":ParagraphStyle("cellm",fontName="Lato",fontSize=8.5,leading=11,textColor=MUTED),
"num":ParagraphStyle("num",fontName="Lato",fontSize=10,leading=12,textColor=INK,alignment=1),
"numb":ParagraphStyle("numb",fontName="Lato-Bold",fontSize=10,leading=12,textColor=INK,alignment=1),
"big":ParagraphStyle("big",fontName="Lato-Bold",fontSize=15,leading=18,textColor=OLIVE,alignment=1),
"hd":ParagraphStyle("hd",fontName="Lato-Bold",fontSize=8.5,leading=11,textColor=colors.white,alignment=1),
"hdl":ParagraphStyle("hdl",fontName="Lato-Bold",fontSize=8.5,leading=11,textColor=colors.white)}
P=lambda t,s="p": Paragraph(t,st[s]); fmt=lambda n: f"{n:,}".replace(","," ")
TIERS=[("1–5",1.00,2990),("6–15",0.85,2540),("16–30",0.70,2090),("31–45",0.58,1730),("46+",0.50,1490)]
def tstyle(extra=[]): return TableStyle([("BACKGROUND",(0,0),(-1,0),INK),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LINEBELOW",(0,1),(-1,-1),0.5,LINE),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),6)]+extra)
def grid():
    rows=[[P("Пиццерий в подписке","hdl")]+[P(f"{t}","hd") for t,_,_ in TIERS],
          [P("Коэффициент","cellm")]+[P(f"×{k:.2f}".replace(".",","),"cellm") for _,k,_ in TIERS],
          [P("Цена за точку, "+RUB+"/мес","cellb")]+[P(fmt(p),"big") for _,_,p in TIERS],
          [P("Скидка","cellm")]+[P("—" if k==1 else f"−{round((1-k)*100)}%","cellm") for _,k,_ in TIERS]]
    t=Table(rows,colWidths=[44*mm]+[25.2*mm]*5); t.setStyle(tstyle([("BACKGROUND",(0,1),(-1,1),HEAD),("BACKGROUND",(0,2),(-1,2),SOFT),("ALIGN",(1,1),(-1,-1),"CENTER"),("LINEABOVE",(0,-1),(-1,-1),0.8,LINE)])); return t
def money():
    def tier(n):
        for lo,(t,k,p) in zip([46,31,16,6,1],TIERS[::-1]):
            if n>=lo: return t,k,p
    rows=[[P("Размер сети","hdl"),P("Ступень","hd"),P("За точку, "+RUB,"hd"),P("В месяц, "+RUB,"hd"),P("Экономия к базе","hd"),P("Вместо, "+RUB+" (без скидки)","hd")]]
    for n,lbl in [(1,"1 пиццерия"),(3,"3 точки"),(6,"6 точек"),(12,"12 точек"),(25,"25 точек"),(50,"50 точек"),(100,"100 точек")]:
        t,k,p=tier(n); rows.append([P(lbl,"cellb"),P(t,"num"),P(fmt(p),"num"),P(fmt(p*n),"numb"),P("—" if k==1 else f"−{round((1-k)*100)}%","num"),P("—" if k==1 else fmt(2990*n),"cellm")])
    t=Table(rows,colWidths=[30*mm,20*mm,26*mm,30*mm,30*mm,34*mm]); t.setStyle(tstyle([("ALIGN",(1,1),(-1,-1),"CENTER")])); return t
def on_page(c,doc):
    c.saveState(); c.setFillColor(OLIVE); c.rect(0,A4[1]-6*mm,A4[0],6*mm,fill=1,stroke=0); c.setFont("Lato",8); c.setFillColor(MUTED)
    c.drawString(20*mm,10*mm,"Dodotool Касса · Тарифы для маркетплейса Dodo IS · 04.09.2026"); c.drawRightString(A4[0]-20*mm,10*mm,f"стр. {doc.page}"); c.restoreState()
doc=SimpleDocTemplate("/tmp/pdf/Dodotool Касса — тарифы и скидки.pdf",pagesize=A4,leftMargin=20*mm,rightMargin=20*mm,topMargin=18*mm,bottomMargin=18*mm,title="Dodotool Касса — тарифы и скидки",author="Dodotool")
s=[]
s.append(P("Dodotool Касса — тарифы и скидки","h1"))
s.append(P("Учёт наличных в пиццерии: выручка из Dodo IS, деньги у курьеров на руках, закрытие смены со сверкой, чеки и история правок. Подписка помесячная, за каждую подключённую пиццерию, оплата по счёту через ЭДО маркетплейса Dodo IS.","sub"))
s.append(P("Один тариф","h2"))
s.append(P("<b>Касса — 2 990 "+RUB+" за пиццерию в месяц.</b> В тариф входит всё: выручка по каналам из Dodo IS, деньги у курьеров из заказов Dodo IS, закрытие смены со сверкой (калькулятор купюр, монеты по весу), передача смены под подпись, чек к каждой операции, история правок и удалений, сейф и инкассация, сводка касс по сети. Пробный период — 14 дней, без ограничений по функциям. Пользователей на точку — без ограничений: менеджеры смен, управляющий, владелец входят через Dodo IS.","p"))
s.append(P("Скидка за количество пиццерий","h2"))
s.append(P("Цена за точку снижается с ростом сети. Ступень определяется числом пиццерий в подписке Dodotool Касса и применяется ко всем точкам сразу — единая цена на всю сеть, а не «первые пять по полной, остальные со скидкой».","p"))
s.append(grid()); s.append(Spacer(1,3*mm))
s.append(P("Первая ступень начинается уже с 6-й точки — это типичная сеть из 6–12 пиццерий, которую рыночная сетка (скидка от 11 или 46 точек) держит на полной цене. Крупная сеть от 46 точек платит половину базовой цены открыто, без индивидуального торга.","p"))
s.append(KeepTogether([P("Что это значит для сети","h2"),money()])); s.append(Spacer(1,2*mm))
s.append(P("Суммы за месяц до НДС-особенностей площадки. Для сравнения: Google-таблица бесплатна, но час менеджера на сведение кассы каждый день и разбор недостач раз в месяц стоят сети заметно больше 100 "+RUB+" в день на точку.","small"))
s.append(P("Правила","h2"))
for t in ["Ступень пересчитывается автоматически при добавлении или удалении пиццерий из подписки — сразу, на текущий расчётный период и все последующие.",
"Новые пиццерии сети не подключаются сами: их добавляют в подписку в «Мои подписки» → «Изменить подписку»; можно подключить часть точек сети.",
"Триал не требует оплаты и не продлевается автоматически: по окончании 14 дней подписка переходит в платную только после подтверждения.",
"Скидки для сетей, подключающих также Dodotool Финансы и Пульс, — по договорённости с площадкой; тарифы разных приложений на маркетплейсе не суммируются."]:
    s.append(P("•  "+t,"p"))
doc.build(s,onFirstPage=on_page,onLaterPages=on_page); print("ok")
