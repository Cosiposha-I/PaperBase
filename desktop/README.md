# PaperBase Desktop (Electron-оболочка)

Electron — только оболочка вокруг Python-ядра `paperbase`. Main-процесс запускает
`python -m paperbase serve` дочерним процессом и показывает уже существующий
веб-интерфейс в нативном окне.

## Статус (честно)

**Работает сейчас (проверено end-to-end на Windows, включая портативный Python —
не системный):**
- `npm run fetch-python` скачивает python-build-standalone под текущую платформу в
  `resources/pyapp/` (структура и версии проверены вручную на реальных релизах,
  см. `scripts/fetch-python.js`).
- Полный мастер первого запуска на портативном Python: venv → torch (CUDA/CPU/MPS) →
  зависимости → модель bge-m3 → проверка → запуск сервера → реальный ответ API.
- Выбор места установки (вся программа, не только «тяжёлые данные») и папки со
  статьями — нативные диалоги.
- Корректное завершение Python-процесса при закрытии окна.
- CI (`.github/workflows/build.yml`): собирает `.exe` и `.dmg` на `windows-latest` +
  `macos-latest` (macOS иначе собрать нельзя — electron-builder требует mac для dmg),
  результат — Artifacts запуска (без публикации в Releases).

**Не сделано в этой итерации:**
- Иконка приложения (`build/icon.ico` / `.icns`) — не добавлена, electron-builder
  использует дефолтную. Пришлите исходник (png ≥512×512) — сделаю оба формата.
- Реальный запуск CI (`build.yml`) на GitHub ещё не проверялся — синтаксис
  проверен по документации electron-builder, но не факт.
- macOS-путь (venv/torch/MPS) проверен только по документации, не на реальном Mac —
  собранный `.dmg` должен по-прежнему протестировать живой пользователь.
- Подписи кода нет (см. `УСТАНОВКА.md` — инструкция обхода SmartScreen/Gatekeeper).

## Разработка

```powershell
cd desktop
npm install
npm start
```

При первом запуске откроется мастер установки (использует системный Python —
на машине разработчика он должен быть в PATH). Отметка «установка завершена»
хранится в `%LOCALAPPDATA%\PaperBase\setup-complete.json` (Windows) /
`~/Library/Application Support/PaperBase/setup-complete.json` (macOS) — чтобы
пройти мастер заново, удалите этот файл (или всю папку `PaperBase` в userData).

## Структура

- `src/main.js` — жизненный цикл приложения, IPC-обработчики мастера установки.
- `src/installer.js` — шаги первого запуска (venv, torch, зависимости, модель).
- `src/server.js` — поиск свободного порта, запуск `paperbase serve`, ожидание готовности.
- `src/paths.js` — все пути (portable Python, runtime venv, userData) в одном месте.
- `src/preload.js` — безопасный мост IPC для `setup.html` (contextIsolation).
- `src/setup.html` + `setup-renderer.js` — экран первого запуска.
