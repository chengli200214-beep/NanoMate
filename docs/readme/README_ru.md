# NanoMate

Персональный помощник Windows с журналом задач и исследовательским модулем с доказательствами.

[Главная проекта](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe"><img src="https://img.shields.io/badge/Download-Windows_x64-c47759" alt="Download Windows x64"></a>
  <a href="../../docs/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="../../docs/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**NanoMate** объединяет диалоги, планы, напоминания, документы и исследования. Опишите цель, проверьте шаги и отслеживайте результат в постоянном журнале. Эта адаптация [nanoMuse](https://github.com/nano-muse/nanoMuse) добавляет отдельное пространство Windows и сохраняет исследовательский модуль.

## Интерфейс

Снимки предпросмотра интерфейса получены в отдельном автономном профиле. Задачи, репозитории и отчёты вымышлены и не являются оценкой модели. Откройте изображение в полном размере.

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="Главная страница и диалоги" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="Журнал задач и подтверждения"></a><br><b>Журнал задач и подтверждения</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="Условия исследования и версии отчёта"></a><br><b>Условия исследования и версии отчёта</b></td>
  </tr>
</table>

## Возможности

| Раздел | Назначение |
| --- | --- |
| Диалоги | Текстовые модели DeepSeek, несколько диалогов, поиск заголовков, светлая и тёмная темы. |
| Журнал задач | Цели, шаги, версии плана, выполнение и результаты сохраняются после перезапуска. |
| Планы и напоминания | Создание и изменение напоминаний и периодических задач с историей изменений. |
| Документы | Чтение Markdown и создание материалов с источниками и проверкой содержимого. |
| Предпочтения | Явно подтверждённые предпочтения, срок действия и удаление. |
| Управляемые действия | Предпросмотр файловых операций, наблюдение браузера и ограниченные действия Windows, с паузой и ручным управлением. |
| Исследования | Снимки источников, доказательства и версии отчётов, пересмотр при изменении условий. |
| Подтверждения и расход | Подтверждение действий, проверка неясных результатов и общие постоянные лимиты расходов. |

## Начало работы

1. Скачайте и запустите [установщик Windows x64 без подписи](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe) или соберите приложение ниже. Пакет содержит Python и Chromium.
2. Откройте **Connections** в настройках или выборе модели. Выберите DeepSeek, `https://api.deepseek.com`, `deepseek-flash` и сохраните ключ в локальном зашифрованном хранилище.
3. Настройте дневной лимит и лимит задачи в `[assistant_usage]` файла `%APPDATA%\nanoMuse Assistant\assistant.toml`. Начальные значения нулевые. Выберите проверенную ценовую политику и сумму резервирования: [руководство](../../docs/assistant.md#local-runtime-usage-limits). Не храните там ключи.
4. Выберите **Runtime → Use configured model** либо выйдите через трей и запустите `nanoMuse-Assistant.exe --live`. Сохранение ключа не меняет режим и не разрешает расходы.
5. Опишите цель, проверьте план и действия, затем следите за **Task ledger**. При необходимости включите паузу или ручное управление.

> Новые профили используют отмеченные автономные ответы по сценарию. Это не локальная языковая модель.

## Примеры запросов

- Составь три учебные цели на неделю и список для подведения итогов.
- Обобщи Markdown-заметки со ссылками на источники.
- Покажи план организации файлов и дождись подтверждения.
- Сравни два репозитория по моим условиям и сохрани доказательства.

## Сборка в Windows

Нужны Windows x64, Git, Node.js 22, Python 3.13 и uv. Запускайте из корня репозитория. Скрипт подготовит зависимости JavaScript, Chromium и Python.

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/NanoMate.git
cd NanoMate
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

Приложение создаётся в `desktop/assistant/dist/win-unpacked/`, установщик в `desktop/assistant/dist/`. `-Target dir` создаёт распакованную версию. Локальные установщики не подписаны. См. [руководство](../../docs/assistant-desktop.md).

## Данные и выполнение

- Electron запускает локальный сервис Python. Диалоги, задачи, напоминания и версии исследований хранятся в файлах и SQLite.
- Нужный контекст отправляется поставщику модели. Локальное хранение не означает, что облачные запросы остаются на компьютере.
- После закрытия окна приложение остаётся в трее. Выход останавливает его сервис. Профиль можно указать через `--profile="D:\AssistantData\profile"`; старые каталоги только для исследований автоматически не импортируются.

## Текущие границы

- Ветка ориентирована на Windows x64; код мобильных приложений, relay и Harness сохранён.
- Изолированный профиль поддерживает проверенные текстовые адаптеры DeepSeek и детерминированные действия Windows. Диктовка, визуальное управление моделью и память на embeddings не включены.
- Напоминания требуют работающего процесса и бодрствующего компьютера. Восстановление новой архитектуры после сна и блокировки не заявлено как проверенное.

## Документация

- [Работа с приложением, сборка и данные](../../docs/assistant-desktop.md)
- [Планы, подтверждения и лимиты](../../docs/assistant.md)
- [Архитектура](../../docs/architecture.md)
- [Уведомления исходного проекта и третьих сторон](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## Лицензия и авторство

Основано на nanoMuse, лицензия **GPL-3.0-or-later**. Уведомления сохранены в [LICENSE](../../LICENSE), [NOTICE](../../NOTICE) и [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). DeepSeek Harness остаётся отдельной точкой входа. Правила: [CONTRIBUTING.md](../../CONTRIBUTING.md).

<details>
<summary>Участники сообщества исходного проекта</summary>

<!-- contributors:start -->
<p>
<a href="https://github.com/wsvn53"><img src="https://avatars.githubusercontent.com/u/912204?v=4&s=48" width="48" height="48" alt="wsvn53"></a>
<a href="https://github.com/cutey-none"><img src="https://avatars.githubusercontent.com/u/47075002?v=4&s=48" width="48" height="48" alt="cutey-none"></a>
<a href="https://github.com/Mola-maker"><img src="https://avatars.githubusercontent.com/u/229602071?v=4&s=48" width="48" height="48" alt="Mola-maker"></a>
<a href="https://github.com/PovedaAqui"><img src="https://avatars.githubusercontent.com/u/9494679?v=4&s=48" width="48" height="48" alt="PovedaAqui"></a>
<a href="https://github.com/jiangkaiqi2005"><img src="https://avatars.githubusercontent.com/u/184961141?v=4&s=48" width="48" height="48" alt="jiangkaiqi2005"></a>
<a href="https://github.com/Ichiro786"><img src="https://avatars.githubusercontent.com/u/158778170?v=4&s=48" width="48" height="48" alt="Ichiro786"></a>
<a href="https://github.com/hobostay"><img src="https://avatars.githubusercontent.com/u/110803307?v=4&s=48" width="48" height="48" alt="hobostay"></a>
<a href="https://github.com/aliabbas6622"><img src="https://avatars.githubusercontent.com/u/193962983?v=4&s=48" width="48" height="48" alt="aliabbas6622"></a>
<a href="https://github.com/cypggs"><img src="https://avatars.githubusercontent.com/u/3694954?v=4&s=48" width="48" height="48" alt="cypggs"></a>
</p>
<!-- contributors:end -->

</details>
