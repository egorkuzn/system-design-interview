#!/usr/bin/env python3
"""Run one bounded GitHub Issue through a local Ollama model."""

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional


ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS_DIR = ROOT / "harness"
RUNS_DIR = HARNESS_DIR / "runs"
DEFAULT_CONTEXT_FILES = (ROOT / "README.md", ROOT / "AGENTS.md")
MODEL_LABEL_PREFIX = "model:"


def run_command(command: List[str], *, timeout: int = 60) -> str:
    import subprocess

    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"Команда не найдена: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Команда превысила timeout {timeout} секунд: {command[0]}") from error

    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Команда завершилась с кодом {completed.returncode}: {details}")

    return completed.stdout


def run_ollama(host: str, model: str, prompt: str, timeout: int) -> str:
    endpoint = host.rstrip("/") + "/api/generate"
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 16384,
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ollama API недоступен по адресу {endpoint}: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama API вернул некорректный JSON") from error

    if result.get("error"):
        raise RuntimeError(f"Ollama API: {result['error']}")
    text = str(result.get("response", "")).strip()
    if not text:
        raise RuntimeError("Ollama API вернул пустой ответ")
    return text


def load_issue(number: int) -> Dict[str, object]:
    raw = run_command(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--json",
            "number,title,body,url,labels,state",
        ],
        timeout=30,
    )
    return json.loads(raw)


def model_from_issue(issue: Dict[str, object]) -> Optional[str]:
    labels = issue.get("labels", [])
    for label in labels:
        name = str(label.get("name", ""))
        if name.startswith(MODEL_LABEL_PREFIX):
            return name[len(MODEL_LABEL_PREFIX) :]
    return None


def read_text(path: pathlib.Path, *, max_chars: int) -> str:
    content = path.read_text(encoding="utf-8")
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n[Контекст обрезан harness]\n"


def build_prompt(issue: Dict[str, object], source_paths: List[pathlib.Path]) -> str:
    contract = read_text(HARNESS_DIR / "agent-prompt.md", max_chars=12_000)

    project_parts = []
    for path in DEFAULT_CONTEXT_FILES:
        if path.exists():
            limit = 8_000 if path.name == "README.md" else 4_000
            project_parts.append(f"## Файл {path.name}\n\n{read_text(path, max_chars=limit)}")

    source_parts = []
    for path in source_paths:
        resolved = path.resolve()
        if not resolved.is_file():
            raise RuntimeError(f"Source pack не найден: {path}")
        source_parts.append(
            f"## Source pack: {resolved.name}\n\n{read_text(resolved, max_chars=40_000)}"
        )

    sources = "\n\n".join(source_parts) if source_parts else (
        "Source pack не предоставлен. Используй только контекст проекта и текст Issue. "
        "Не изображай внешний поиск."
    )

    return f"""{contract}

# Контекст проекта

{chr(10).join(project_parts)}

# Текущая задача

- Issue: #{issue['number']}
- URL: {issue['url']}
- Название: {issue['title']}
- Статус: {issue['state']}

## Тело Issue

{issue['body']}

# Проверенные входные материалы

{sources}

# Финальная команда

Текущая дата: {dt.datetime.now().astimezone().date().isoformat()}.

Выполни только текущую задачу. Верни только итоговый Markdown по формату из контракта. Не повторяй входные инструкции.
"""


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", value).strip("-").lower()
    return slug[:60] or "result"


def save_result(issue: Dict[str, object], model: str, response: str) -> pathlib.Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    filename = f"issue-{issue['number']}-{timestamp}-{safe_slug(model)}.md"
    path = RUNS_DIR / filename
    header = (
        "<!-- Черновик Ollama. Требует проверки. Не публиковать автоматически. -->\n\n"
        f"# Черновик для Issue #{issue['number']}\n\n"
        f"- Model: `{model}`\n"
        f"- Issue: {issue['url']}\n"
        f"- Generated: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n\n"
    )
    path.write_text(header + response.strip() + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issue", type=int, help="GitHub Issue number")
    parser.add_argument("--model", help="Override model label from the Issue")
    parser.add_argument(
        "--sources",
        action="append",
        default=[],
        type=pathlib.Path,
        help="Verified Markdown source pack; may be repeated",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print prompt and do not call Ollama")
    parser.add_argument("--timeout", type=int, default=900, help="Ollama timeout in seconds")
    parser.add_argument("--host", default="http://127.0.0.1:11434", help="Ollama API base URL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        issue = load_issue(args.issue)
        if issue.get("state") != "OPEN":
            raise RuntimeError(f"Issue #{args.issue} не открыт")

        model = args.model or model_from_issue(issue)
        if not model:
            raise RuntimeError("Не указана модель и отсутствует label model:<name>")

        prompt = build_prompt(issue, args.sources)
        if args.dry_run:
            print(prompt)
            return 0

        response = run_ollama(args.host, model, prompt, args.timeout)
        result_path = save_result(issue, model, response)
        print(result_path)
        return 0
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

