# ZCode Engineering Hub Lite

Лайт-дистрибутив стандарта Engineering Hub: правила безопасности, дисциплина git
(обучающая), скиллы, страж guard и два плагина. Рассчитан на **Windows** и
**свежий профиль ZCode** (при существующей настройке установщик попросит `--force`
и сделает бэкапы). Полный стандарт живёт в основном хабе на машине-источнике.

## Что входит

| Компонент | Что даёт |
|---|---|
| `AGENTS-L0.md` → `<ZCODE_DIR>/AGENTS.md` | глобальные правила: русский язык, секреты, подтверждения опасных операций, пояснение команд новичку |
| `skills/` (3) | `git-start-task` / `git-finish-task` — обучающий git-цикл (ветка → работа → обычный merge); `project-onboarding` — карта проекта |
| `templates/` → `<ZCODE_DIR>/templates/` | эталоны: контекст задачи, project.yaml, AGENTS L1, ADR, .gitignore |
| `hooks/guard.py` | страж: блокирует чтение вне рабочего корня, запись в секреты, push без разрешения (режимы shadow/enforce) |
| `plugins/` | adversarial-review (независимое ревью кода; нужен свой API-ключ) и guided-research (глубокие исследования; ключ не нужен) |

## Предусловия (ставятся до ZCode-части)

1. **git for Windows** — включает **Git Bash** (нужен самому ZCode-агенту и
   adversarial-review: там есть `grep`, `sha256sum`, `diff`, `sed`, `/tmp`).
2. **Python 3.10+** — для стража и установщика (доступен как `python` или `py -3`).
3. **ZCode** — установлен и запущен хотя бы раз (должен существовать каталог
   конфигурации; по документации ZCode это подкаталог `.zcode` домашнего каталога
   пользователя, на Windows — `%USERPROFILE%\.zcode`).
4. **Доступ к этому приватному репозиторию** (клон через git).
5. **Свой API-ключ (BYOK)** для плагина adversarial-review — настроить заранее:
   провайдер (например, OpenRouter) и ключ в настройках ZCode. Без ключа
   adversarial-review работать не будет; **guided-research ключа не требует** —
   если ключа пока нет, начните без adversarial-review и добавьте его позже.

## Установка

В Git Bash, в каталоге клона этого репозитория:

```bash
# 1. Посмотреть план (ничего не пишет)
python install_lite.py

# 2. Установить; сразу задать рабочий корень — каталог ваших проектов
#    (страж будет считать чтение вне него нарушением)
python install_lite.py --apply --strict-fs-root "C:/Users/<user>/work"

# 3. Перезапустить ZCode
```

Установщик кладёт файлы в `<ZCODE_DIR>`, подставляя реальный путь вместо
плейсхолдера `<ZCODE_DIR>`, подключает PreToolUse-хук стража и оставляет маркер
`<ZCODE_DIR>/.zcode-hub-lite.json`. Всё перезаписываемое копируется в
`<ZCODE_DIR>/backup/lite-<дата>/`.

Затем **плагины** (UI ZCode):

4. **Plugin Marketplace → Discover → `+`** → добавить локальный маркетплейс —
   путь к `plugins/` внутри клона этого репозитория → Add.
5. Установить: **Adversarial Review** и **Guided Research** → проверить, что оба
   видны в Settings → Plugins.
6. Новая сессия: проверить, что команда `/guided-research` распознаётся, а скилл
   `git-start-task` отвечает на просьбу «начни задачу».

## Страж: обязательный ввод в строй

По умолчанию страж в режиме **shadow** (только лог). Порядок ввода — в
`hooks/README.md`: неделя shadow → `guard.py --doctor` (должно быть
`guard registered in PreToolUse: True` и 0 ошибок `hook.run.failed` — это
подтверждает, что хук **фактически вызывается**) → `--report 7` → включение
`enforce`. Не включайте enforce, не убедившись, что хук вызывается.

## Как работать (git-цикл для новичка)

1. «Начни задачу <что сделать>» → агент создаст ветку `feature/<имя>` и файл
   задачи `.zcode/tasks/<дата>-<имя>.md`, объясняя каждую команду.
2. Работа: агент делает изменения, поясняя команды git по ходу.
3. «Заверши задачу» → агент покажет diff, закоммитит (осмысленные сообщения,
   формат `feat|fix|docs: ...`), предложит merge в main — **только с вашего
   явного «да»**; push — отдельное разрешение.
4. `/guided-research <тема>` — глубокое исследование с вопросами и отчётом .md.

## Обновление лайт-хаба

```bash
git pull                       # в каталоге клона
python install_lite.py --apply # пере-установка поверх (по маркеру, без --force)
```

Плагины: при изменениях в `plugins/` поднимите `version` в
`.zcode-plugin/plugin.json` плагина **и синхронно** в `plugins/marketplace.json`,
затем UI: Marketplace Sources → **Refresh** → у плагина → **Update**.

## Откат

```bash
python install_lite.py --uninstall --yes                 # снять hook-entry из config.json
python install_lite.py --uninstall --yes --restore-files # + вернуть файлы из последнего бэкапа
```

Оба шага требуют перезапуска ZCode. Файлы и логи стража остаются в
`<ZCODE_DIR>/hooks/guard/` (удаляются вручную, если нужно). Бэкапы — в
`<ZCODE_DIR>/backup/lite-*/`.

## Что здесь разрабатывается

Правила репозитория — `AGENTS.md`, манифест — `.zcode/project.yaml`. Задачи
ведутся по тому же стандарту: `git-start-task` → ветка → `git-finish-task`.
Изменения guard.py — только вместе с зелёными `hooks/test_guard.py` и
`hooks/test_winpaths.py`.

## Границы verified/unverified

Проверено на машине-источнике (Linux): 162 теста регресса стража, 29 unit-тестов
Windows-путей (логика `under()` без realpath), изолированная установка
end-to-end. **Не проверено эмпирически** (первая Windows-машина): фактический
вызов PreToolUse-хука ZCode на Windows, realpath-поведение путей, smoke-прогон
adversarial-review в Git Bash — всё это шаги этапа развёртывания (см.
`hooks/README.md` и чек-лист ввода в строй).
