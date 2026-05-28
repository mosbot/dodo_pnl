# POC: миграция на `POST /api/v2/reports/opu`

Дата: 2026-05-28. Спайк-валидация перед написанием плана миграции.

## Что проверяли

- Pizza: Кубинка-1 (PlanFact `projectId=584301`), key `id=1` (PiX).
- Период: апрель 2026 (`2026-04-01` … `2026-04-30`).
- Метод: `accrual` (=`isCalculation: true` в v2).

Дёрнули `POST https://api.planfact.io/api/v2/reports/opu` с
`reportGenMethod: "Projects"`, сравнили выход с production-логикой
`app/pnl.build_pnl` на тех же входных операциях.

## Результат

**Чистая прибыль идентична до копейки: `1 444 482.55 ₽`.**

| Строка | `build_pnl` | v2 reports/opu | Δ |
| ------ | ----------- | -------------- | - |
| UC | 2 755 514.91 | 2 755 514.91 | 0 |
| LC | 2 471 904.05 | 2 471 904.05 | 0 |
| DC | 997 805.00 | 997 805.00 | 0 |
| RENT | 557 739.43 | 557 739.43 | 0 |
| FRANCHISE | 860 249.12 | 860 249.12 | 0 |
| TAX | 329 076.00 | 329 076.00 | 0 |
| **NET_PROFIT** | **1 444 482.55** | **1 444 482.55** | **0** |

### Расхождения по двум строкам — не баги, а разная агрегация

- **Revenue**: у нас 8 885 088 (только `REVENUE`), у v2 8 893 088
  («Доходы» целиком, включая 8 000 ₽ «Излишек при закрытии смены»).
  У нас этот излишек уходит в `OTHER_INCOME` — это правильнее для P&L.
- **MARKETING**: у нас 32 731, у v2 49 711. v2 кладёт в «Маркетинг»
  в т.ч. подкатегорию «Рекламные материалы»; у нас она по нашему
  шаблону уходит в другую строку.

Оба расхождения — артефакты **нашего custom P&L-template**, не v2.
v2 отдаёт сырую иерархию категорий PlanFact, наш `template_path_to_code`
её перепаковывает по-своему.

## Что это даёт по объёму

- **Raw operations approach (текущий):** 956 операций × ~30 КБ JSON
  ≈ 30 МБ ответа за один месяц одного проекта PiX. На XFood-ключе с
  30+ проектами это уходит в десятки МБ × парallel-split на под-периоды.
- **v2 reports/opu (предлагается):** один POST, ответ ≈ 80 КБ за
  месяц всего ключа со всеми проектами.

**Фактор уменьшения трафика: ~350× на типичный месяц одного проекта.**
Дополнительно уходит весь `_fetch_ops_recursive` с date-range split —
там был самый хрупкий код клиента (комментарий S11.1 в `planfact.py:215`).

## Открытый вопрос — обработка клиентов без настроенного шаблона ОПУ

Не проверено: что возвращает `POST /api/v2/reports/opu`, если у
пользователя в PlanFact UI не настроена иерархия ОПУ-категорий
(только default-набор). Гипотезы:

- (а) возвращает defaults — миграция полностью прозрачна;
- (б) возвращает пустую `incomeItems` / `outcomeItems` — нужно fallback
  на raw operations;
- (в) возвращает 403 / access-error — тогда детектим и fallback.

Перед миграцией нужно создать «голый» ключ без настроенного шаблона и
проверить эмпирически.

## Architecture sketch миграции

Меняется только источник агрегата:

```python
# Было (planfact.py):
operations = await pf.fetch_all_operations(date_start, date_end,
                                            project_ids, method="accrual")
# build_pnl(... operations=operations ...) — внутри parts aggregation

# Будет:
report = await pf.report_opu(date_start, date_end,
                             project_ids, is_calculation=True)
aggregates = flatten_v2_leaves(report["operationCategoryByProjects"])
# build_pnl(... cached_aggregates=aggregates ...) — уже есть cached_aggregates path
```

`build_pnl` уже умеет работать в режиме `cached_aggregates` (см.
`pnl.py:547-565`). Это значит интеграция требует:

1. Новый метод `PlanFactClient.report_opu()` в `planfact.py`.
2. Адаптер `_v2_to_aggregates(report)` — собрать payload в формате,
   совместимом с `cached_aggregates`:
   `{"totals": {pid|code: amt}, "cat_totals": {pid|cid: amt},
     "revenue_by_channel": {pid: {channel: amt}},
     "active_project_ids": [...]}`.
3. Feature-flag на пользователя / на ключ: `use_v2_reports = true|false`
   (на случай fallback).
4. Метрика-инвариант: сравнить `revenue` и `net_profit` v2 vs raw в
   shadow-mode пару дней до полного переключения.

Шаблон, классификатор категорий, маппинг проектов, таргеты и весь
остальной слой остаются нетронутыми.

## Файлы POC

- `_scratch/v2-projects.json` — raw ответ v2 для `reportGenMethod: Projects`.
- `_scratch/v2-category.json` — для `OperationCategory`.
- `_scratch/build-pnl.json` — выход production `build_pnl` для сравнения.
- `_scratch/run_build_pnl_poc.py` — скрипт, проверявший build_pnl на VPS.

## Решение

POC валидирован: net profit сходится, структура совместима. Можно
писать план миграции при условии, что открытый вопрос (клиенты без
шаблона ОПУ) решится в одну сторону. До закрытия этого вопроса —
**raw operations не выпиливаем, а оставляем как fallback**.
