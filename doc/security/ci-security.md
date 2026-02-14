# CI Security Pipeline

## Обзор

Security pipeline интегрирован в CI/CD и запускается при каждом push и pull request.

## Инструменты

### 1. pip-audit — Проверка CVE в зависимостях
- **Что делает:** Сканирует установленные Python-пакеты на известные уязвимости (CVE).
- **Когда:** Каждый push/PR.
- **Команда:** `pip-audit --desc --skip-editable`
- **Действие при находке:** CI падает, обновить зависимость.

### 2. bandit — Python SAST-сканер
- **Что делает:** Статический анализ Python-кода на уязвимости (hardcoded secrets, SQL injection, insecure functions).
- **Когда:** Каждый push/PR.
- **Конфигурация:** `pyproject.toml` секция `[tool.bandit]`.
- **Команда:** `bandit -r src/ -c pyproject.toml`
- **Отчёт:** JSON-файл `bandit-report.json` (артефакт CI).
- **Критерий блокировки:** HIGH severity + HIGH confidence.

### 3. gitleaks — Поиск секретов в коде
- **Что делает:** Сканирует git-историю и файлы на наличие секретов (API ключи, пароли, tokens).
- **Когда:** Каждый push/PR.
- **Действие:** CI падает при обнаружении секретов.

### 4. Dependabot — Автоматическое обновление зависимостей
- **Что делает:** Создаёт PR для обновления зависимостей (pip, docker, github-actions).
- **Когда:** Еженедельно (pip, docker), ежемесячно (github-actions).
- **Конфигурация:** `.github/dependabot.yml`.

## Диаграмма pipeline

```
push/PR → lint → security ─┬─ pip-audit (CVE)
                            ├─ bandit (SAST)
                            └─ gitleaks (secrets)
                 test ──────┘
                     ↓
                   build → staging-smoke → deploy
```

## Настройка bandit

Исключения в `pyproject.toml`:
- `B101` (assert) — используется в FastAPI для runtime checks.
- Директории `tests/` и `scripts/` исключены.

## Как реагировать на находки

| Инструмент | Severity HIGH | Severity MEDIUM | Severity LOW |
|------------|--------------|-----------------|--------------|
| pip-audit  | Блокирует CI | Блокирует CI    | Warning      |
| bandit     | Блокирует CI | Warning         | Info         |
| gitleaks   | Блокирует CI | Блокирует CI    | Warning      |
| Dependabot | PR за 24ч    | PR за 1 неделю  | PR за 1 месяц |
