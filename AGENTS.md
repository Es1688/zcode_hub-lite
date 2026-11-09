# AGENTS.md — zcode-hub-lite

## Что это за проект
Лайт-дистрибутив стандарта Engineering Hub для второй машины (Windows): упрощённые
скиллы git-цикла для новичка, эталонные шаблоны, Windows-порт стража guard и плагины
(adversarial-review, guided-research). Один клон + один установщик `install_lite.py`.
Полный стандарт живёт в основном хабе `~/work/it/zcode` (машина-источник); этот репо
самодостаточен и не ссылается на него путями.

## Структура
| Путь | Что это | Можно менять? |
|---|---|---|
| `skills/` | 3 lite-скилла (git-start-task, git-finish-task, project-onboarding) | да, через задачу |
| `templates/` | эталоны: task-context, project.yaml, AGENTS-L1, ADR, gitignore | да, через задачу |
| `hooks/guard.py` | Windows-порт стража (полный набор правил, shadow → enforce) | да, через задачу + тесты |
| `hooks/test_guard.py`, `hooks/test_winpaths.py` | регресс стража; unit-тесты Windows-путей | да, синхронно с guard.py |
| `install_lite.py` | установщик в `<ZCODE_DIR>` (dry-run по умолчанию) | да, через задачу |
| `AGENTS-L0.md` | устанавливаемый Level 0 lite | да, через задачу |
| `plugins/` | локальный маркетплейс dev-it-zcode + 2 плагина | да, через задачу |
| `.zcode/tasks/` | контексты задач Level 2 | да |

## Ключевые правила
- Никаких абсолютных путей машины-источника и литералов `~/.zcode` в текстах репо —
  только плейсхолдер `<ZCODE_DIR>` (подставляется при установке).
- guard.py меняется только вместе с прогоном `hooks/test_guard.py` (132 теста) и
  `hooks/test_winpaths.py`.
- Windows-специфика, не проверяемая на Linux-машине, фиксируется в README как шаг
  этапа развёртывания (shadow-неделя, проверка факта вызова хука).

## Соглашения
- Язык: код и коммиты — английский; документация — русский.
- Пуш/merge в main — только с подтверждения пользователя (Level 0 + project.yaml).
