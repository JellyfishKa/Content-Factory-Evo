# content-factory-lite

Лёгкий resumable контент-конвейер `planner → researcher → writer → editor → seo`.
Правила вынесены в код (валидаторы) и конфиг, а не в промпт — это защита от
оверфиттинга планировщика.

## Архитектура

```
src/
  orchestrator.py   # resumable раннер (CLI)
  pipeline.py       # гранулярный per-stage API (для панели)
  app.py            # Streamlit human-in-the-loop панель
  viewer.py         # FastAPI вьювер результатов
  llm.py            # OpenAI-совместимый httpx-клиент + фоллбек моделей
  research.py       # бесплатный веб-поиск (DuckDuckGo / Wikipedia / auto)
  enrich.py         # подкрепление готового текста фактами из ссылок
  db.py             # postgres: runs, scenarios, artifacts, checks
  validators.py     # STYLE-гейт (чистые функции)
  schemas.py        # Pydantic-контракты
  stages/           # planner, researcher, writer, editor, seo
prompts/            # CRAFT+ промпты по стадиям
references/         # референсы тона для editor
inputs/             # тестовые источники
runs/               # выходные артефакты (.md)
```

## Запуск

### Локально без докера (ноут)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; на Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # затем впиши эндпоинт/ключ
python src/orchestrator.py --input inputs/transcript.txt
```

### Перезапуск только редактуры/seo

```bash
python src/orchestrator.py --input inputs/transcript.txt --from-stage editor
```

### Docker (ноут или VPS)

```bash
docker compose up --build       # на VPS: docker compose up -d --build
pytest -q                       # тесты валидаторов
```

### Панель (human-in-the-loop)

```bash
streamlit run src/app.py        # :8501 — старт прогона, правка стадий,
                                # подкрепление готового текста ссылками
```

## Текущий статус

Боевые все стадии: `planner / researcher / writer / editor / seo`. Реализовано:

- **Веб-поиск исследователя** — бесплатные провайдеры DuckDuckGo / Wikipedia /
  `auto`-цепочка (`research.py`), без API-ключей, с retry/backoff.
- **Фоллбек LLM-моделей** — на 429/5xx/пустой ответ клиент по очереди пробует
  запасные free-модели (`llm.py`, `model_fallbacks` в конфиге).
- **STYLE-гейт** в коде (`validators.py`) — чёрный список, цифра в первом
  экране, длина, капс, длина предложений, хештег-блок.
- **Разделение пост/статья** — per-kind «голос» editor'а + `style.voice` в
  конфиге; статья = лонгрид, пост = компактный с хештег-блоком.
- **Подкрепление ссылками** (`enrich.py`) — готовый текст + URL → фетч и
  вплетение фактов либо блок «Источники».
- **Resumable** orchestrator + гранулярный per-stage rerun из панели.

68 тестов (pytest), все зелёные.

Дальше (идеи): кэш веб-поиска, CI на пуш, метрики прогона (токены/время),
новые форматы вывода (Telegram-тред, рассылка).
