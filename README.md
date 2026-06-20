# content-factory-lite

Лёгкий resumable контент-конвейер `planner → researcher → writer → editor → seo`.
Правила вынесены в код (валидаторы) и конфиг, а не в промпт — это защита от
оверфиттинга планировщика.

## Архитектура

```
src/
  orchestrator.py   # resumable раннер
  llm.py            # OpenAI-совместимый httpx-клиент
  db.py             # postgres: runs, scenarios, artifacts, checks
  schemas.py        # Pydantic-контракты
  stages/           # planner, researcher, writer, editor, seo
prompts/            # CRAFT+ промпты по стадиям
references/         # референсы для editor
inputs/             # тестовые источники
runs/               # выходные артефакты
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

## Текущий статус

Реализовано: `llm.py`, `db.py`, `schemas.py`, `stages/planner.py` и
`stages/editor.py` (боевые), `validators.py` + тесты, orchestrator
(resumable, STYLE-гейт). Стадии `researcher`/`writer`/`seo` пока отдают
placeholder-артефакты — пайплайн проходит end-to-end.

Дальше: боевые `researcher`/`writer`/`seo` на LLM, полный прогон на реальном
эндпоинте и развёртывание в Docker на VPS.
