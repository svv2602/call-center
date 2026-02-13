# Call Center AI — Документация

## Структура документации

```
doc/
├── README.md                          # Этот файл — навигация
│
├── business/                          # Бизнес-документация
│   ├── presentation.md                # Презентация проекта (для инвесторов / руководства)
│   ├── business-case.md               # Бизнес-кейс: ROI, финансовая модель, окупаемость
│   └── roadmap.md                     # Дорожная карта: фазы, сроки, Gantt-диаграмма
│
├── technical/                         # Техническая документация
│   ├── architecture.md                # Архитектурный обзор (SAD): компоненты, стек, обоснования
│   ├── data-model.md                  # Схема данных (ERD): таблицы, связи, индексы
│   ├── sequence-diagrams.md           # Диаграммы последовательности: потоки всех сценариев
│   ├── deployment.md                  # Диаграмма развёртывания: инфраструктура, Docker Compose
│   └── nfr.md                         # Нефункциональные требования: производительность, безопасность
│
├── security/                          # Безопасность и compliance
│   ├── threat-model.md                # Модель угроз (STRIDE): анализ по компонентам
│   ├── data-policy.md                 # Политика обработки персональных данных
│   └── risk-matrix.md                 # Матрица рисков: оценка, приоритизация, план действий
│
├── audit-report.md                    # Аудит-отчёт и рекомендации (внешняя экспертиза)
│
└── development/                       # Документация для разработки
    ├── 00-overview.md                 # Общий обзор: архитектура, стек, структура проекта
    ├── phase-1-mvp.md                 # Фаза 1: подбор шин, наличие, AudioSocket, STT/TTS
    ├── phase-2-orders.md              # Фаза 2: статус заказа, оформление, CallerID
    ├── phase-3-services.md            # Фаза 3: шиномонтаж, консультации, RAG
    ├── phase-4-analytics.md           # Фаза 4: дашборд, качество, A/B тесты, оптимизация
    ├── api-specification.md           # Спецификация Store API (все фазы, примеры)
    └── deployment-guide.md            # Руководство по развёртыванию (пошаговое)
```

## Для кого какие документы

| Аудитория | Документы |
|-----------|-----------|
| **Инвестор / CEO** | `business/presentation.md` → `business/business-case.md` |
| **Технический директор / CTO** | `business/roadmap.md` → `technical/architecture.md` → `security/risk-matrix.md` |
| **Архитектор / Tech Lead** | `technical/` (все) → `development/00-overview.md` |
| **Разработчик** | `development/` (все) |
| **DevOps** | `technical/deployment.md` → `development/deployment-guide.md` |
| **Безопасность / DPO** | `security/` (все) |

## Диаграммы

Все диаграммы выполнены в формате **Mermaid** и рендерятся:
- В GitHub / GitLab (нативная поддержка)
- В VS Code (расширение Markdown Preview Mermaid)
- В любом Mermaid-совместимом просмотрщике
