# Guard (страж) — инструкция для lite-установки

Страж — это PreToolUse-хук ZCode: он проверяет каждую команду агента до выполнения
и блокирует опасное (запись в секреты, чтение вне рабочего корня, push без
разрешения). Устанавливается скриптом `install_lite.py` (корневой README, шаг 2).

## Режимы (`<ZCODE_DIR>/hooks/guard/guard.json`, ключ `mode`)

- `shadow` — только логирует решения, ничего не блокирует (по умолчанию);
- `enforce` — реально блокирует (deny/ask);
- `off` — выключен.

## Обязательный путь ввода в строй (не пропускать)

1. **Неделя в shadow**: работайте как обычно; страж пишет лог в
   `<ZCODE_DIR>/hooks/guard/guard.jsonl`.
2. **Проверка самодиагностики** (Git Bash / терминал):
   ```
   python "<ZCODE_DIR>/hooks/guard/guard.py" --doctor
   python "<ZCODE_DIR>/hooks/guard/guard.py" --report 7
   ```
   `--doctor` должен показать `mode: shadow`, `guard registered in PreToolUse: True`
   и `0 line(s) with config.file.invalid / hook.run.failed` в логе ZCode — это
   подтверждает, что хук **фактически вызывается**.
3. `--report` — посмотреть, что flagged; ложные срабатывания обсудить с куратором.
4. **Включение enforce**: перезаписать в guard.json `"mode": "enforce"` (или
   переустановить с `install_lite.py --apply --guard-mode enforce`), перезапустить
   ZCode, снова `--doctor`.

## Рабочий корень (strict_fs_root)

Граница чтения задаётся при установке: `install_lite.py --apply --strict-fs-root
"<путь к рабочему каталогу>"` (например `C:\Users\<user>\work`). После этого чтение
вне корня блокируется (правило G-READ-OUTSIDE, Level C). Разовое разрешение на
10 минут — файл `allow-level-c` в каталоге стража.

## Что страж НЕ ловит

Это страховка от ошибок агента, не защита от целенаправленного обхода: он не видит
внутрь скриптов, base64-трюков и команд PowerShell/cmd (они только логируются как
NOTE-raw-shell). Полный набор правил — в шапке `guard.py`.
