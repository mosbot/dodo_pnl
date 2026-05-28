# ПланФакт API — DOCUMENTS для AI-агента

Этот файл покрывает document endpoints: `invoice-documents`, файлы / вложения счетов и шаблоны нумерации.

`README.md` — policy, router, budgets, cache.  
`QUICKSTART.md` — короткий technical index.  
`USE_CASES.md` — бизнес-семантика долга и просрочки.  
`LOOKUPS.md` — как добывать `accountId`, `contrAgentId`, `companyId`.

## Когда открывать

- пользователь просит счета на оплату;
- нужен список неоплаченных или просроченных счетов;
- нужно создать, изменить или удалить счет на оплату;
- нужен PDF-документ по счету;
- нужен файл / вложение / ссылка на скачивание по уже известному `invoiceDocumentId`, `attachedDocumentId` или `hash`;
- нужен итог по счетам на оплату без расшифровки;
- нужен шаблон или номер счета перед созданием `invoice-document`.

## Граница раздела

- `invoice-documents` = счета на оплату и связанные document/file routes.
- `invoice-documents` не равно общей дебиторке / кредиторке бизнеса.
- Если пользователь говорит только про должников, кредиторов, просрочку или долг без слов `счет`, `invoice`, `счета на оплату` — не уводить запрос в `invoice-documents`.
- Если пользователь просит общую дебиторку / кредиторку по КА, это не `DOCUMENTS.md`, а `LOOKUPS.md` (`GET /contragents/calculated/{id}` и при необходимости `{id}/additional`); просрочка / долги с нестандартным срезом — `USE_CASES.md` + `OPERATIONS.md`.
- Если пользователь просит только счета на оплату или их статусы, это `DOCUMENTS.md`.
- Если пользователь просит «оплаты по счетам» — `invoice-documents` может дать сами счета, но не является источником всех платежей; для операций оплаты идти в `OPERATIONS.md` или `DEALS.md` по контексту.

## Ключевые endpoint'ы

### Основные документы

- `GET /api/v1/invoice-documents`
- `GET /api/v1/invoice-documents/summary`
- `GET /api/v1/invoice-documents/{invoiceDocumentId}`
- `POST /api/v1/invoice-documents`
- изменение и удаление документа по идентификатору смотреть в official `apidoc` только если пользователь просит update/delete или поле, которого нет в локальном наборе

### Связанные document endpoint'ы

- `GET /api/v1/invoice-documents/{invoiceDocumentId}/document`
- `GET /api/v1/invoice-documents/document/shared`
- `GET /api/v1/invoice-documents/{invoiceDocumentId}/files`
- `GET /api/v1/attached-documents/{attachedDocumentId}`

### Шаблоны и номера

- `GET /api/v1/invoice-templates`
- `POST /api/v1/invoice-templates/generate-invoice-documents-number`
- `GET /api/v1/invoice-templates/invoice-document-pass-through-number`

## Быстрый выбор источника

| Запрос пользователя                                                      | Использовать                                                         | Не использовать                                  |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------- | ------------------------------------------------ |
| «покажи счета», «список счетов», «счета на оплату по КА / дате / сделке» | `GET /api/v1/invoice-documents`                                      | `attached-documents`, `invoice-templates`        |
| «сколько счетов», «общая сумма счетов», «сводка по счетам»               | `GET /api/v1/invoice-documents/summary`                              | полный список счетов, если расшифровка не нужна  |
| «PDF счета», «документ счета» по известному `invoiceDocumentId`          | `GET /api/v1/invoice-documents/{invoiceDocumentId}/document`         | список счетов, если ID уже известен              |
| «документ по ссылке / hash», `shared invoice document`                   | `GET /api/v1/invoice-documents/document/shared`                      | список счетов                                    |
| «файлы счета», «печать / подписи / логотип счета»                        | `GET /api/v1/invoice-documents/{invoiceDocumentId}/files`            | PDF endpoint, если нужны именно файлы оформления |
| «скачай вложение», «дай файл по `attachedDocumentId`»                    | `GET /api/v1/attached-documents/{attachedDocumentId}`                | `invoice-documents`, если это generic-вложение   |
| «шаблон номера счета»                                                    | `GET /api/v1/invoice-templates`                                      | список счетов                                    |
| «сгенерируй номер счета»                                                 | `POST /api/v1/invoice-templates/generate-invoice-documents-number`   | создание счета                                   |
| «следующий сквозной / дневной / месячный / годовой номер»                | `GET /api/v1/invoice-templates/invoice-document-pass-through-number` | создание счета                                   |

Минимальные read-only маршруты:

- список счетов на оплату → `GET /api/v1/invoice-documents` с server-side фильтрами из contract, если пользователь дал период, КА, статус или сделку;
- только количество / сумма → `GET /api/v1/invoice-documents/summary`;
- PDF по известному `invoiceDocumentId` → `GET /api/v1/invoice-documents/{invoiceDocumentId}/document`;
- файлы оформления по известному `invoiceDocumentId` → `GET /api/v1/invoice-documents/{invoiceDocumentId}/files`;
- generic-вложение по известному `attachedDocumentId` → `GET /api/v1/attached-documents/{attachedDocumentId}`.

Для этих read-only маршрутов не открывать `apidoc`, если уже известен нужный ID/hash и не требуется редкое поле ответа.

## Основные правила

- использовать `InvoiceDocuments`, а не legacy `Invoices`, если нужен актуальный список счетов на оплату;
- `invoice-documents` покрывает счета на оплату, а не всю дебиторку / кредиторку бизнеса;
- просроченный счет на оплату не равен полной просроченной дебиторке компании;
- `invoice-documents/summary` использовать до списка, если пользователю нужен только итог: количество счетов и сумма. Не загружать полный список ради count / sum.
- `invoice-documents/{id}/files` возвращает файлы оформления счета: подписи, печать, логотип. Это не PDF счета и не список вложений операции.
- `attached-documents/{id}` использовать только когда уже есть `attachedDocumentId`. Не искать через него счета, операции или документы.
- `invoice-documents/document/shared` использовать только когда пользователь дал `hash` / shared-ссылку. Не использовать для поиска счетов.
- `invoice-templates/*` использовать только для нумерации и шаблонов. Эти методы не создают счет, не показывают оплату и не дают финансовую аналитику.

## Сценарии

### Список счетов на оплату

- использовать `GET /api/v1/invoice-documents`;
- показывать неоплаченные, просроченные или отфильтрованные счета;
- критерии просрочки и `as-of date` называть явно, если пользователь просит список просроченных счетов или счетов должников.

### Сводка по счетам на оплату

- использовать `GET /api/v1/invoice-documents/summary`, если нужен только итог по счетам;
- endpoint возвращает количество счетов и сумму счетов по фильтрам;
- не использовать для расшифровки, файлов, PDF или статусов отдельных счетов.

### Создать счет на оплату

- endpoint: `POST /api/v1/invoice-documents`
- до запроса нужны счет, контрагент, даты, номер и `items[]`
- номер может быть задан пользователем или сгенерирован через `POST /api/v1/invoice-templates/generate-invoice-documents-number`
- не создавать документ без строк

### PDF по счету

- если есть `invoiceDocumentId` и нужен PDF / документ самого счета — использовать `GET /api/v1/invoice-documents/{invoiceDocumentId}/document`;
- если есть `hash` shared-документа — использовать `GET /api/v1/invoice-documents/document/shared`;
- если нужны файлы оформления счета — использовать `GET /api/v1/invoice-documents/{invoiceDocumentId}/files`;
- если есть `attachedDocumentId` generic-вложения — использовать `GET /api/v1/attached-documents/{attachedDocumentId}`;
- не перебирать список счетов ради файла, если ID или hash уже известен.

### Нумерация счетов

Если пользователь просит создать счет и номер не задан явно:

1. получить номер через `POST /api/v1/invoice-templates/generate-invoice-documents-number` с `billingDate`;
2. показать номер пользователю или использовать его в `POST /api/v1/invoice-documents`, если создание уже подтверждено;
3. не считать генерацию номера созданием счета.

Backend считает номер по активному шаблону. Если активного шаблона нет, создаёт или активирует шаблон по умолчанию. При расчете учитываются новые `invoice-documents` и legacy invoices, чтобы номер не пересекался.

`GET /api/v1/invoice-templates/invoice-document-pass-through-number` нужен только для просмотра следующих счетчиков номера: дневной, месячный, годовой, сквозной. Для создания конкретного номера использовать `generate-invoice-documents-number`.

## Known caveats

- legacy `Invoices` не использовать по умолчанию;
- не подменять общей задолженностью список invoice documents;
- `invoice-documents/{id}/document` — документ / PDF счета;
- `invoice-documents/{id}/files` — файлы оформления счета, не PDF;
- точные поля document/file endpoint'ов смотреть в official `apidoc`, только если локального описания недостаточно для ответа или write-payload; не открывать `apidoc` для обычного списка, summary, PDF или файла по известному ID.
