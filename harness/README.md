# Ollama research harness

Локальный harness запускает небольшие исследовательские задачи из GitHub Issues через Ollama.

Он намеренно не даёт модели доступ к GitHub CLI, токенам, файловой системе или публикации комментариев. Скрипт сам читает Issue, передаёт модели ограниченный контекст и сохраняет черновик для проверки человеком.

## Поток работы

```text
GitHub Issue
    ↓
run.py собирает ограниченный prompt
    ↓
Ollama формирует черновик
    ↓
harness/runs/issue-<number>-<timestamp>.md
    ↓
проверка ссылок и фактов координатором
    ↓
публикация результата вручную
```

## Запуск

Показать prompt без обращения к модели:

```bash
python3 harness/run.py 19 --dry-run
```

Запустить модель, указанную в label `model:*`:

```bash
python3 harness/run.py 19
```

Переопределить модель:

```bash
python3 harness/run.py 19 --model llama3
```

Добавить заранее проверенный пакет источников:

```bash
python3 harness/run.py 25 --sources harness/source-packs/issue-25.md
```

Полные `README.md` и `AGENTS.md` по умолчанию не передаются: для узких задач это лишний контекст. Если задача действительно зависит от них:

```bash
python3 harness/run.py 19 \
  --sources harness/source-packs/issue-19.md \
  --include-project-files
```

Для leaf-задачи, которая должна вернуть одну заполненную строку таблицы:

```bash
python3 harness/run.py 47 \
  --model owl/t-lite:latest \
  --sources harness/source-packs/issue-14-system-design-primer.md \
  --require-table-columns 6
```

При нарушении контракта ответ сохраняется для диагностики, а runner завершается с кодом `2`.

## Ограничения

- Модель не должна самостоятельно искать источники в интернете.
- Задачи, требующие поиска, запускаются только после подготовки source pack.
- Результат модели является черновиком и не публикуется автоматически.
- Закрытие Issue, изменение roadmap и коммиты выполняет координатор после проверки.
- Файлы из `harness/runs/` не коммитятся.

