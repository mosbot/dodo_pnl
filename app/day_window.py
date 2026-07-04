"""Расчёт временных окон для страницы `/board`.

Бизнес-логика «vs прошлая неделя тот же день до этого часа» (и LFL месяца)
требует ровно одинаковых окон по длительности — иначе сравнение нечестное.
Все вычисления — в MSK (Europe/Moscow), потому что Dodo IS отдаёт данные
в зоне заведения, для РФ-сети это всегда MSK.

Окна, которые считаем:

  TODAY      [today 00:00 → now_floor_hour]     — текущий день к этому часу
  LW         [LW   00:00 → LW_floor_hour]       — тот же день прошлой недели
                                                  до того же часа (для сравнения дня)
  MTD        [today.month-01 00:00 → now_floor] — месяц до этого часа
  MTD_LFL    [LY.same_day  00:00 → LY_floor]    — месяц прошлого года до того же
                                                  дня и часа (для LFL месяца)
  LY_MONTH   [LY.month-01 → LY.last_day 23:00]  — весь прошлый месяц LY full
                                                  (используется для прогноза)

Floor-округление часа: если сейчас 12:34, то to=12:00 (НЕ 13:00, чтобы
данные были «уже произошедшим фактом», не прогнозом до конца часа).

Для пользователя «прошл. среда» = ближайшая прошлая среда (то есть −7 дней).
В нашей реализации это просто `now - timedelta(days=7)` — date arithmetic
сохранит день недели автоматически.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Europe/Moscow без сезонных переводов — фиксированный +03:00.
MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """Текущее время в MSK. Отдельная функция для удобства тестов
    (можно подменить через monkeypatch)."""
    return datetime.now(MSK)


def _floor_to_hour(dt: datetime) -> datetime:
    """Сбросить минуты, секунды, микросекунды."""
    return dt.replace(minute=0, second=0, microsecond=0)


def _round_to_nearest_hour(dt: datetime) -> datetime:
    """Округлить к ближайшему часу. Правило:
    `:30` включительно → floor (вниз). `:31` и далее → ceil (вверх).
    Используется для baseline-окон (last_week, mtd_lfl), чтобы они были
    как можно ближе к текущему моменту, но оставались hour-aligned
    (требование Dodo IS productivity / читаемость в UI).
    """
    base = dt.replace(minute=0, second=0, microsecond=0)
    if dt.minute > 30 or (dt.minute == 30 and (dt.second > 0 or dt.microsecond > 0)):
        base += timedelta(hours=1)
    return base


def _last_day_of_month(d: date) -> date:
    """Последний день месяца этой даты."""
    if d.month == 12:
        first_of_next = date(d.year + 1, 1, 1)
    else:
        first_of_next = date(d.year, d.month + 1, 1)
    return first_of_next - timedelta(days=1)


def _shift_year(d: date, years: int) -> date:
    """Сместить дату на N лет, корректно обрабатывая 29 февраля
    (даже редкое — нечего ронять прод раз в 4 года)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # 29 Feb → year без 29 февраля. Сдвигаем на 28.
        return d.replace(year=d.year + years, day=28)


@dataclass(frozen=True)
class Window:
    """ISO-формат для Dodo IS: 'YYYY-MM-DDTHH:00:00' (без timezone, локальный MSK)."""

    from_: datetime
    to: datetime

    def to_dodois(self) -> tuple[str, str]:
        """Строки, готовые для query-параметров Dodo IS API."""
        return (
            self.from_.strftime("%Y-%m-%dT%H:%M:%S"),
            self.to.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    @property
    def hours(self) -> int:
        """Длительность окна в часах (для проверки равенства окон сравнения)."""
        delta = self.to - self.from_
        return int(delta.total_seconds() // 3600)


@dataclass(frozen=True)
class BoardWindows:
    """Полный набор окон для расчёта страницы `/board`.

    `today` и `last_week` — для блока «День».
    `mtd` и `mtd_lfl` — для блока «Месяц LFL».
    `last_year_full_month` — для расчёта прогноза по формуле
        forecast = mtd × (LY_full / MTD_LFL)
    """

    now: datetime  # «сейчас» MSK (с минутами)
    today: Window  # с 00:00 текущего дня до floor(now, hour)
    last_week: Window  # ровно за -7 дней, одинаковая длина
    mtd: Window  # с 1-го по ВЧЕРА (завершённые дни; сегодня — в плитке `today`)
    mtd_lfl: Window  # тот же завершённый диапазон год назад
    last_year_full_month: Window  # полный прошлый год тот же месяц

    @property
    def to_hour(self) -> str:
        """«до 12:00» для UI."""
        return self.today.to.strftime("%H:%M")

    @property
    def today_date(self) -> date:
        return self.today.from_.date()

    @property
    def last_week_date(self) -> date:
        return self.last_week.from_.date()

    @property
    def last_year_month(self) -> str:
        """'YYYY-MM' прошлого года, того же месяца. Полезно как ключ кэша."""
        return self.last_year_full_month.from_.strftime("%Y-%m")


def compute_board_windows(now: Optional[datetime] = None) -> BoardWindows:
    """Главная функция: посчитать все 5 окон от текущего момента MSK.

    `now` можно подать для тестов (timezone-aware MSK); в проде передавать None.
    """
    if now is None:
        now = now_msk()
    elif now.tzinfo is None:
        # Сырое naive — считаем что MSK
        now = now.replace(tzinfo=MSK)
    else:
        now = now.astimezone(MSK)

    # today: ровно «сейчас» (с минутами). Источник total = accounting/sales,
    # которое принимает arbitrary precision; productivity для сегодняшнего
    # окна не подходит (требует hour-align). UI показывает «до HH:MM».
    to_today = now.replace(second=0, microsecond=0)
    from_today = to_today.replace(hour=0, minute=0)

    # last_week: ближайший час к (now - 7д). ≤:30 — вниз, >:30 — вверх.
    # Так baseline максимально близко к текущему моменту и остаётся
    # hour-aligned для других endpoint'ов.
    lw_now = now - timedelta(days=7)
    to_lw = _round_to_nearest_hour(lw_now)
    from_lw = to_lw.replace(hour=0, minute=0)

    # MTD/MTD_LFL — по ЗАВЕРШЁННЫМ дням (с 1-го по ВЧЕРА). Сегодняшний неполный
    # день из месячного окна исключаем: иначе LFL-прогноз качается в течение
    # дня — наш сегодня неполный, а прошлогодний тот же день уже ПОЛНЫЙ, значит
    # отношение MTD/MTD_LFL занижено утром и «догоняет» к вечеру. Сегодня видно
    # отдельной плиткой `today`. Месячный endpoint работает по дате.
    from_mtd = from_today.replace(day=1)
    end_completed = to_today - timedelta(days=1)  # вчера (та же минута)
    if end_completed < from_mtd:
        # 1-е число: завершённых дней месяца ещё нет → прогноз ненадёжен,
        # оставляем окно на 1-е (вырожденный случай).
        end_completed = from_mtd
    to_mtd = end_completed

    # MTD_LFL: тот же ЗАВЕРШЁННЫЙ диапазон −1 год (симметрично, до вчера LY).
    from_mtd_lfl = datetime.combine(
        _shift_year(from_mtd.date(), -1), from_mtd.time(), tzinfo=MSK,
    )
    to_mtd_lfl = datetime.combine(
        _shift_year(end_completed.date(), -1), end_completed.time(), tzinfo=MSK,
    )

    # Полный прошлый месяц LY (для прогноза):
    # с 1-го числа до 23:00 последнего дня (LFL-проjection делит на это число)
    ly_first_day = _shift_year(from_mtd.date(), -1)
    ly_last_day = _last_day_of_month(ly_first_day)
    from_ly_full = datetime.combine(ly_first_day, datetime.min.time(), tzinfo=MSK)
    to_ly_full = datetime.combine(
        ly_last_day, datetime.min.time().replace(hour=23), tzinfo=MSK,
    )

    return BoardWindows(
        now=now,
        today=Window(from_today, to_today),
        last_week=Window(from_lw, to_lw),
        mtd=Window(from_mtd, to_mtd),
        mtd_lfl=Window(from_mtd_lfl, to_mtd_lfl),
        last_year_full_month=Window(from_ly_full, to_ly_full),
    )


def forecast_month(
    mtd_revenue: float,
    mtd_lfl_revenue: float,
    last_year_full_revenue: float,
    *,
    fallback_days_in_month: Optional[int] = None,
    fallback_days_passed: Optional[int] = None,
) -> tuple[Optional[float], str]:
    """LFL-projection с pace-fallback'ом.

    Основная формула (LFL):  forecast = mtd × (LY_full / MTD_LFL)
    Fallback на pace:        forecast = mtd × (days_in_month / days_passed)

    Возвращает (forecast, method), где method ∈ {"lfl", "pace", "none"}.
    """
    if mtd_revenue <= 0:
        return (None, "none")

    if mtd_lfl_revenue > 0 and last_year_full_revenue > 0:
        return (mtd_revenue * last_year_full_revenue / mtd_lfl_revenue, "lfl")

    if fallback_days_in_month and fallback_days_passed and fallback_days_passed > 0:
        return (
            mtd_revenue * float(fallback_days_in_month) / float(fallback_days_passed),
            "pace",
        )

    return (None, "none")
