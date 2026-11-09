# PORT.md — Порт adversarial-review на ZCode (вариант B, плагин)

> **Lite-копия.** Копия плагина из основного хаба (машина-источник, `~/work/it/zcode/`)
> с одной адаптацией для Git Bash на Windows: команды `rg` заменены на `grep -E`
> (ripgrep не входит в git for windows; `sha256sum`, `diff`, `sed`, `/tmp` в Git Bash
> есть). Upstream-эталон `adversarial-review-master/` и ADR-001 живут в основном
> хабе — здесь их нет, для работы плагина они не нужны.

Перенос скилла `adversarial-review` с Claude Code + Codex CLI на ZCode по варианту B
из ADR-001 основного хаба («нативный сабагент-ревьюер»). Обоснование выбора: E6 ❌
(Codex CLI не установлен — единственный блокер варианта A), B доступен без внешних
зависимостей; проверено на ZCode 3.14.3-7762.

**Форма доставки — локальный плагин** `adversarial-review` (исправление после живого
теста 2026-09-29: standalone-каталога `~/.zcode/agents/` в discovery НЕТ — кастомные
агенты регистрируются только как компонент `agents` плагина, что подтверждено
эталоном `documents:visual-judge` из официального плагина documents).

## Раскладка

```
<клон zcode-hub-lite>/plugins/
├── marketplace.json                # каталог локального маркетплейса dev-it-zcode
└── adversarial-review/             # копия плагина (dir name == name в манифесте)
    ├── .zcode-plugin/plugin.json   # name, version, skills/commands/agents
    ├── skills/adversarial-review/
    │   ├── SKILL.md                # оркестратор (EN, хирургический порт)
    │   └── references/prompts.md   # шаблоны task-секций и истории раундов
    ├── commands/adversarial-review.md  # тонкая обёртка с $ARGUMENTS
    ├── agents/adversarial-reviewer.md       # read-only ревьюер
    ├── agents/adversarial-reviewer-exec.md  # + Bash (sandbox:workspace-write)
    └── PORT.md                     # этот файл
```

## Архитектура

| Уровень | Мастер (Claude Code) | Этот порт (ZCode, вариант B) |
|---|---|---|
| Lead (оркестратор, фиксы) | Claude, главный поток | Главный агент ZCode |
| Раннер | Sonnet-сабагент, читает `references/runner.md`, дергает `codex exec` | **Упразднён** — его функции разделены между lead'ом и ревьюером |
| Ревьюер | Codex CLI (`codex exec --json`, sandbox через bwrap) | Плагин-агент `adversarial-review:adversarial-reviewer` (BYOK `openrouter/moonshotai/kimi-k2.7-code`, `tools: [Read, Grep, Glob]`) или `…reviewer-exec` (+ `Bash`) |

Ключевое усиление безопасности: базовый ревьюер **физически read-only** (исчерпывающий
список инструментов), тогда как в мастере codex по умолчанию работал в
`workspace-write`, и защита была только программной. Снапшоты мутаций сохранены как
второй рубеж и как единственный рубеж для exec-варианта.

Frontmatter агент-файлов повторяет проверенный эталон официального плагина
(`name`, `description`, `model`, `thoughtLevel`, `tools` YAML-списком). Поля
`maxTurns`/`injectAgentsMd` не включены — в эталоне отсутствуют, поддержка не
подтверждена (задокументированы в F8 доков, живой проверки не было).

## Маппинг механизмов

| Механизм мастера | Вариант B |
|---|---|
| `codex exec -m <model> -s <sandbox> -c …` | Agent tool `subagent_type: adversarial-review:adversarial-reviewer[-exec]`; модель/thoughtLevel — в файле агента (E11: per-call `model` не существует) |
| `codex exec resume <session-id>` | Нет — каждый раунд fresh-dispatch; раунды 2+ несут полную историю в промпте (шаблон fresh-exec из Step 7.4 мастера стал основным путём) |
| Двухканальный результат (JSON 11 полей + `RUNNER_RESULT_AT:`) | Ревью = финальное сообщение сабагента; lead сам валидирует и пишет его в `/tmp/ar-review-<ID>.md` |
| Session-маркер `<!-- ADVERSARIAL-REVIEW-SESSION -->`, поиск rollout'ов в `~/.codex/sessions` | Удалены (нет сессий); `ATTEMPT_ID` упразднён, остаётся `REVIEW_ID` |
| bwrap preflight | Удалён |
| Triage раннера (JSON: finding_count, max_severity, …) | Lead считает severity-счётчики одним `grep -Ec` (lite: rg → grep -E для Git Bash) (для сигнала Step 6.5) |
| `review_quality` (valid/degraded_*) | Backstop в sanity-чеках lead'а: REVISE без severity-тегов или «unable to review»-заглушка → `format_invalid` → один retry |
| Retry раннера (2 codex-вызова на раунд) | Один dispatch + максимум один re-dispatch на раунд за счёт lead (не тратит счётчик раундов) |
| Fallback-цепочка resume→fresh-exec | Свёрнута: повторный сбой dispatch в раунде ≥ 2 → терминальный NOT VERIFIED; в раунде 1 → ABORT |
| `user_warning` из раннера | Удалён (нет раннера); ограничения ревьюер сообщает внутри текста ревью (виден verbatim) |
| Канал `/tmp/codex-*` | `/tmp/ar-*`: `ar-plan`, `ar-body`, `ar-diff`, `ar-review`, `ar-git-pre/post`, `ar-inputs-pre/post` |
| Резолв `RUNNER_SPEC_PATH` (3 тира `~/.claude/...`) | Резолв `references/prompts.md`: база скилла из листинга сессии → plugin cache → `~/.zcode/skills/...` |
| Ревьюер сам читает diff (`git diff` в песочнице) | Lead материализует diff в `/tmp/ar-diff-<ID>.patch`; ревьюер Read'ает его + сами файлы в `REPO_ROOT` |

## Грамматика вызова

```
/adversarial-review [plan|code|<file-path>] [low|medium|high|xhigh] [sandbox:read-only|workspace-write]
```

- `sandbox:read-only` (дефолт) → `adversarial-review:adversarial-reviewer` (read-only, без verify-команд)
- `sandbox:workspace-write` → `adversarial-review:adversarial-reviewer-exec` (+ Bash для тестов/линтеров)
- **Удалены:** `model:<name>` (модель — строка `model:` в агент-файле плагина), `approvals:*`,
  `sandbox:danger-full-access`, `sandbox:inherit` — концепты Codex CLI.
- `low|medium|high|xhigh` — advisory: передаётся ревьюеру в dispatch-блоке
  (`REASONING_EFFORT`); thoughtLevel самого агента фиксирован в файле (`high`).

## Установка (UI, один раз)

1. **Plugin Marketplace → Add → Add Plugin Marketplace** → вставить корень маркетплейса:
   `<клон zcode-hub-lite>/plugins` → Add (см. README репозитория).
2. **Personal → dev-it-zcode → Adversarial Review** → **Install** (после установки
   появится в Settings → Plugins; включён по умолчанию).
3. Новая сессия/задача — скилл, команда и оба агента подхватываются автоматически.

Никаких symlink'ов в `~/.zcode` больше не используется (предыдущий symlink-подход
работал для skills/commands, но НЕ регистрирует агентов — см. коррекцию F8 в ADR).

## Обновление после правок исходника

Правки в `plugins/adversarial-review/` не подхватываются автоматически:

1. Поднять `version` в `.zcode-plugin/plugin.json` и синхронно в `marketplace.json`.
2. UI: Plugin Marketplace → шестерёнка → **Marketplace Sources** → **Refresh** у `dev-it-zcode`.
3. UI: Personal → adversarial-review → **Update** → проверить версию.
4. Новая сессия для проверки.

## Смена модели ревьюера

Одна строка `model:` в обоих `agents/*.md` (формат `providerId/modelId` — сверяйся
со списком моделей хоста) + обновление плагина (см. выше). Требование варианта B:
модель ревьюера — **другого семейства**, чем модель lead'а (диверсификация — смысл
adversarial-петли). Если BYOK-провайдер недоступен, скилл честно абортится
(no silent fallback), а не подменяет ревьюера той же моделью.

## Известные отличия и ограничения

- **Нет resume** — каждый раунд платит за полную историю (токены растут с раундами).
  Компенсация: ревьюер каждый раз независим, не накапливает якорений сессии.
- **Plan Mode** (ZCode): записи в `/tmp` блокируются → body передаётся инлайном в
  dispatch-промпте, снапшоты и cleanup пропускаются, exec-ревьюер не используется.
  Детект plan-режима — по системному сообщению «Plan mode is active» (совпадает с
  Claude Code).
- **Bash-команды exec-ревьюера ≤ 10 минут**: при исчерпании max-timeout вызов уходит в
  auto-background (E5) и результат недоступен ревьюеру — в файле агента это запрещено
  явно, верификация дробится на короткие команды.
- **>50 файлов / diff > 2 МБ**: lead пишет в diff-файл `--stat`-сводку + полный diff,
  рекомендует exec-вариант; предупреждение оператору о риске мелкого ревью.
- Кастомные сабагенты — beta-поверхность ZCode; проверенная версия 3.14.3-7762.
- `maxTurns`/`injectAgentsMd` не используются (не подтверждены в этой сборке);
  ограничение объёма работы ревьюера — через инструкцию в теле агента.

## Соответствие чек-листу ADR-001 §6

- [x] Раскладка: плоские имена — плагин `adversarial-review`, скилл `skills/adversarial-review/`
- [x] Замена Plan Mode-детекта — работает формулировка ZCode «Plan mode is active»
- [x] Замена `subagent_type/model` на кастомный агент — плагин-агенты `adversarial-review:adversarial-reviewer[-exec]`
- [x] Замена путей `~/.claude/...` — резолв `references/prompts.md` через базу скилла/plugin cache
- [x] Временные файлы: `/tmp` работает вне Plan mode (E14); в Plan mode — инлайн-body
- [x] Гейт структурных исправлений — batch-pause сохранён (см. SKILL.md Step 6.3)
- [x] `description` ≤ 1024 симв. — ~369 симв.
- [x] Вынос промптов в `references/` — `references/prompts.md` (роль ревьюера — в файлах агентов)
