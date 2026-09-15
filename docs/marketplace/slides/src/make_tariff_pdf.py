"""Тарифные PDF для маркетплейса — в фирменных цветах модулей.

    python3 make_tariff_pdf.py kassa     → «Dodotool Касса — тарифы и скидки.pdf»   (оливковый)
    python3 make_tariff_pdf.py finance   → «Dodotool Финансы — тарифы и скидки.pdf» (синий)

Цвета берём из самих приложений: Касса #4a6b1a (шапка «Смена открыта»),
Финансы #1d4ed8 (акцент модуля в шапке Dodotool). Ступени скидок —
коэффициенты ×1,00 / 0,85 / 0,70 / 0,58 / 0,50, округление до десятков.
"""
import sys, os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)
from reportlab.lib.styles import ParagraphStyle

F = "/usr/share/fonts/truetype/lato/"
pdfmetrics.registerFont(TTFont("Lato", F + "Lato-Regular.ttf"))
pdfmetrics.registerFont(TTFont("Lato-Bold", F + "Lato-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Lato-Light", F + "Lato-Light.ttf"))
pdfmetrics.registerFont(TTFont("DJ", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFontFamily("Lato", normal="Lato", bold="Lato-Bold",
                              italic="Lato", boldItalic="Lato-Bold")
RUB = '<font name="DJ">₽</font>'
DATE = "15.09.2026"
TIER_LABELS = ["1–5", "6–15", "16–30", "31–45", "46+"]
COEF = [1.00, 0.85, 0.70, 0.58, 0.50]

MODULES = {
    "kassa": dict(
        file="Dodotool Касса — тарифы и скидки.pdf",
        title="Dodotool Касса — тарифы и скидки",
        accent="#4a6b1a", soft="#eef3e6", ink="#141a0e",
        lead=("Учёт наличных в пиццерии: выручка из Dodo IS, деньги у курьеров на "
              "руках, закрытие смены со сверкой, чеки и история правок. Подписка "
              "помесячная, за каждую подключённую пиццерию, оплата по счёту через "
              "ЭДО маркетплейса Dodo IS."),
        plans=[("Касса", 2490,
                "В тариф входит всё: выручка по каналам из Dodo IS, деньги у курьеров из "
                "заказов Dodo IS, закрытие смены со сверкой (калькулятор купюр, монеты по "
                "весу), передача смены под подпись, чек к каждой операции, история правок "
                "и удалений, сейф и инкассация, сводка касс по сети.")],
        main_plan=0,
        compare=("Для сравнения: Google-таблица бесплатна, но час менеджера на сведение "
                 "кассы каждый день и разбор недостач раз в месяц стоят сети заметно "
                 "больше 100 " + RUB + " в день на точку."),
        rules_tail=("Скидки для сетей, подключающих также Dodotool Финансы и Пульс, — по "
                    "договорённости с площадкой; тарифы разных приложений на маркетплейсе "
                    "не суммируются."),
    ),
    "finance": dict(
        file="Dodotool Финансы — тарифы и скидки.pdf",
        title="Dodotool Финансы — тарифы и скидки",
        accent="#1d4ed8", soft="#dbeafe", ink="#0f172a",
        lead=("Все метрики каждой пиццерии из Dodo IS на одном экране: выручка, кухня, "
              "доставка, рейтинги, история по месяцам, цели и цветовые зоны. Пульс — вся "
              "сеть сегодня. Подписка помесячная, за каждую подключённую пиццерию, оплата "
              "по счёту через ЭДО маркетплейса Dodo IS."),
        plans=[
            ("Финансы", 1490,
             "Карточка месяца по каждой пиццерии и по сети, метрики Dodo IS (кухня, "
             "доставка, курьеры, средний чек), рейтинги клиентов, РКО и РС, цели и "
             "цветовые зоны, история метрик по месяцам и год к году. С подключённым "
             "PlanFact добавляются себестоимость, ФОТ, прибыль и полный P&L по статьям."),
            ("Пульс", 990,
             "Живая сводка дня по всей сети: выручка каждой пиццерии против того же дня "
             "прошлой недели, месяц против прошлого года, активные стопы по каналам, "
             "секторам и продуктам, скорость кухни и доставки."),
            ("Финансы и Пульс", 1990,
             "Оба модуля на 20 % дешевле их суммы. Один вход через Dodo IS, общие роли, "
             "заведения и настройки."),
        ],
        main_plan=2,
        compare=("Суммы за месяц до НДС-особенностей площадки. Тариф «Финансы и Пульс» "
                 "выгоднее двух отдельных подписок на 490 " + RUB + " с каждой точки."),
        rules_tail=("Скидки для сетей, подключающих также Dodotool Касса, — по "
                    "договорённости с площадкой; тарифы разных приложений на маркетплейсе "
                    "не суммируются."),
    ),
}


# Явные ступени там, где они уже согласованы (округление «красивее»
# арифметического: Финансы 46+ = 750, Пульс 46+ = 490). Остальное считается
# по коэффициентам с округлением до десятков.
EXPLICIT_TIERS = {
    2490: [2490, 2120, 1740, 1440, 1240],   # Касса
    1490: [1490, 1270, 1040, 860, 750],     # Финансы
    990:  [990, 840, 690, 570, 490],        # Пульс
    1990: [1990, 1690, 1390, 1150, 1000],   # Финансы и Пульс
}


def tiers_for(base):
    """Ступени: согласованный список или расчёт по коэффициентам."""
    prices = EXPLICIT_TIERS.get(base) or [int(round(base * k / 10) * 10) for k in COEF]
    return list(zip(TIER_LABELS, COEF, prices))


def build(mod_key):
    M = MODULES[mod_key]
    ACCENT = colors.HexColor(M["accent"]); INK = colors.HexColor(M["ink"])
    SOFT = colors.HexColor(M["soft"])
    MUTED = colors.HexColor("#6b7280"); LINE = colors.HexColor("#e5e7eb")
    HEAD = colors.HexColor("#f5f6f8")

    st = {
        "h1": ParagraphStyle("h1", fontName="Lato-Bold", fontSize=22, leading=27, textColor=INK, spaceAfter=2*mm),
        "sub": ParagraphStyle("sub", fontName="Lato-Light", fontSize=11.5, leading=15, textColor=MUTED, spaceAfter=7*mm),
        "h2": ParagraphStyle("h2", fontName="Lato-Bold", fontSize=13.5, leading=17, textColor=INK, spaceBefore=6*mm, spaceAfter=3*mm),
        "p": ParagraphStyle("p", fontName="Lato", fontSize=10, leading=14, textColor=INK, spaceAfter=2.5*mm),
        "small": ParagraphStyle("small", fontName="Lato", fontSize=8.5, leading=11.5, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName="Lato", fontSize=9.5, leading=12, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName="Lato-Bold", fontSize=9.5, leading=12, textColor=INK),
        "cellm": ParagraphStyle("cellm", fontName="Lato", fontSize=8.5, leading=11, textColor=MUTED),
        "num": ParagraphStyle("num", fontName="Lato", fontSize=10, leading=12, textColor=INK, alignment=1),
        "numb": ParagraphStyle("numb", fontName="Lato-Bold", fontSize=10, leading=12, textColor=INK, alignment=1),
        "big": ParagraphStyle("big", fontName="Lato-Bold", fontSize=15, leading=18, textColor=ACCENT, alignment=1),
        "hd": ParagraphStyle("hd", fontName="Lato-Bold", fontSize=8.5, leading=11, textColor=colors.white, alignment=1),
        "hdl": ParagraphStyle("hdl", fontName="Lato-Bold", fontSize=8.5, leading=11, textColor=colors.white),
    }
    P = lambda t, s="p": Paragraph(t, st[s])
    fmt = lambda n: f"{n:,}".replace(",", " ")

    def tstyle(extra=()):
        return TableStyle([("BACKGROUND", (0, 0), (-1, 0), INK),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LINEBELOW", (0, 1), (-1, -1), 0.5, LINE),
                           ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 6)] + list(extra))

    def grid_single(base):
        """Одна сетка ступеней (модуль с единственным тарифом)."""
        T = tiers_for(base)
        rows = [[P("Пиццерий в подписке", "hdl")] + [P(t, "hd") for t, _, _ in T],
                [P("Коэффициент", "cellm")] + [P(f"×{k:.2f}".replace(".", ","), "cellm") for _, k, _ in T],
                [P("Цена за точку, " + RUB + "/мес", "cellb")] + [P(fmt(p), "big") for _, _, p in T],
                [P("Скидка", "cellm")] + [P("—" if k == 1 else f"−{round((1-k)*100)}%", "cellm") for _, k, _ in T]]
        t = Table(rows, colWidths=[44*mm] + [25.2*mm]*5)
        t.setStyle(tstyle([("BACKGROUND", (0, 1), (-1, 1), HEAD),
                           ("BACKGROUND", (0, 2), (-1, 2), SOFT),
                           ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                           ("LINEABOVE", (0, -1), (-1, -1), 0.8, LINE)]))
        return t

    def grid_multi():
        """Тарифы × ступени (модуль с несколькими тарифами)."""
        rows = [[P("Тариф", "hdl")] + [P(t, "hd") for t in TIER_LABELS]]
        for i, (name, base, _) in enumerate(M["plans"]):
            T = tiers_for(base)
            style = "big" if i == M["main_plan"] else "numb"
            rows.append([P(name, "cellb")] + [P(fmt(p), style) for _, _, p in T])
        rows.append([P("Скидка", "cellm")] + [P("—" if k == 1 else f"−{round((1-k)*100)}%", "cellm") for k in COEF])
        t = Table(rows, colWidths=[44*mm] + [25.2*mm]*5)
        t.setStyle(tstyle([("ALIGN", (1, 1), (-1, -1), "CENTER"),
                           ("BACKGROUND", (0, M["main_plan"]+1), (-1, M["main_plan"]+1), SOFT),
                           ("LINEABOVE", (0, -1), (-1, -1), 0.8, LINE)]))
        return t

    def money(base):
        T = tiers_for(base)

        def tier(n):
            for lo, row in zip([46, 31, 16, 6, 1], T[::-1]):
                if n >= lo:
                    return row
        rows = [[P("Размер сети", "hdl"), P("Ступень", "hd"), P("За точку, " + RUB, "hd"),
                 P("В месяц, " + RUB, "hd"), P("Экономия к базе", "hd"),
                 P("Вместо, " + RUB + " (без скидки)", "hd")]]
        for n, lbl in [(1, "1 пиццерия"), (3, "3 точки"), (6, "6 точек"), (12, "12 точек"),
                       (25, "25 точек"), (50, "50 точек"), (100, "100 точек")]:
            t, k, p = tier(n)
            rows.append([P(lbl, "cellb"), P(t, "num"), P(fmt(p), "num"), P(fmt(p*n), "numb"),
                         P("—" if k == 1 else f"−{round((1-k)*100)}%", "num"),
                         P("—" if k == 1 else fmt(base*n), "cellm")])
        t = Table(rows, colWidths=[30*mm, 20*mm, 26*mm, 30*mm, 30*mm, 34*mm])
        t.setStyle(tstyle([("ALIGN", (1, 1), (-1, -1), "CENTER")]))
        return t

    def on_page(c, doc):
        c.saveState()
        c.setFillColor(ACCENT)
        c.rect(0, A4[1] - 6*mm, A4[0], 6*mm, fill=1, stroke=0)
        c.setFont("Lato", 8)
        c.setFillColor(MUTED)
        c.drawString(20*mm, 10*mm, f"{M['title'].split(' — ')[0]} · Тарифы для маркетплейса Dodo IS · {DATE}")
        c.drawRightString(A4[0] - 20*mm, 10*mm, f"стр. {doc.page}")
        c.restoreState()

    os.makedirs("/tmp/pdf", exist_ok=True)
    doc = SimpleDocTemplate("/tmp/pdf/" + M["file"], pagesize=A4, leftMargin=20*mm,
                            rightMargin=20*mm, topMargin=18*mm, bottomMargin=18*mm,
                            title=M["title"], author="Dodotool")
    s = [P(M["title"], "h1"), P(M["lead"], "sub")]

    multi = len(M["plans"]) > 1
    s.append(P("Тарифы" if multi else "Один тариф", "h2"))
    for name, base, desc in M["plans"]:
        s.append(P(f"<b>{name} — {fmt(base)} {RUB} за пиццерию в месяц.</b> {desc}", "p"))
    s.append(P("Пробный период — 14 дней, без ограничений по функциям. Пользователей на "
               "точку — без ограничений: сотрудники входят через Dodo IS со своими ролями.", "p"))

    s.append(P("Скидка за количество пиццерий", "h2"))
    s.append(P("Цена за точку снижается с ростом сети. Ступень определяется числом пиццерий "
               "в подписке и применяется ко всем точкам сразу — единая цена на всю сеть, а "
               "не «первые пять по полной, остальные со скидкой».", "p"))
    s.append(grid_multi() if multi else grid_single(M["plans"][0][1]))
    s.append(Spacer(1, 3*mm))
    s.append(P("Первая ступень начинается уже с 6-й точки — это типичная сеть из 6–12 "
               "пиццерий, которую рыночная сетка (скидка от 11 или 46 точек) держит на "
               "полной цене. Крупная сеть от 46 точек платит половину базовой цены "
               "открыто, без индивидуального торга.", "p"))

    main_name, main_base, _ = M["plans"][M["main_plan"]]
    s.append(KeepTogether([P(f"Что это значит для сети — тариф «{main_name}»", "h2"),
                           money(main_base)]))
    s.append(Spacer(1, 2*mm))
    s.append(P(M["compare"], "small"))

    s.append(P("Правила", "h2"))
    for t in ["Ступень пересчитывается автоматически при добавлении или удалении пиццерий "
              "из подписки — сразу, на текущий расчётный период и все последующие.",
              "Новые пиццерии сети не подключаются сами: их добавляют в подписку в «Мои "
              "подписки» → «Изменить подписку»; можно подключить часть точек сети.",
              "Триал не требует оплаты и не продлевается автоматически: по окончании "
              "14 дней подписка переходит в платную только после подтверждения.",
              M["rules_tail"]]:
        s.append(P("•  " + t, "p"))

    doc.build(s, onFirstPage=on_page, onLaterPages=on_page)
    print("собран:", M["file"])


if __name__ == "__main__":
    for key in (sys.argv[1:] or ["kassa", "finance"]):
        build(key)
