# nanoMuse Assistant

Un asistente personal para Windows con un registro de tareas y un módulo de investigación con evidencias.

[Página del proyecto](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe"><img src="https://img.shields.io/badge/Download-Windows_x64-c47759" alt="Download Windows x64"></a>
  <a href="../../docs/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="../../docs/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**nanoMuse Assistant** reúne conversaciones, planes, recordatorios, documentos e investigación en una aplicación de escritorio. Describe el objetivo, revisa los pasos y sigue los resultados en un registro persistente. Esta adaptación de [nanoMuse](https://github.com/nano-muse/nanoMuse) añade un espacio independiente para Windows y conserva el módulo de investigación.

## Vistas del producto

Capturas de la vista previa de escritorio actual, con un perfil sin conexión aislado. Las tareas, los repositorios y los informes son ejemplos sintéticos, no resultados de evaluación. Abre cada imagen para verla completa.

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="Inicio y conversaciones" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="Registro de tareas y revisión humana"></a><br><b>Registro de tareas y revisión humana</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="Condiciones e informe de investigación"></a><br><b>Condiciones e informe de investigación</b></td>
  </tr>
</table>

## Funciones

| Área | Función |
| --- | --- |
| Conversaciones | Modelos de texto DeepSeek, varias conversaciones, búsqueda por título y temas claro y oscuro. |
| Registro de tareas | Objetivos, pasos, versiones del plan, ejecución y resultados conservados tras reinicios. |
| Planes y recordatorios | Creación y modificación de recordatorios y tareas periódicas, con cambios registrados. |
| Documentos | Lectura de Markdown local y resultados con referencias y comprobación del contenido. |
| Preferencias | Preferencias confirmadas expresamente, con caducidad y eliminación. |
| Acciones controladas | Vista previa de archivos, observación del navegador y acciones acotadas en Windows, con pausa y control manual. |
| Investigación | Comparación con instantáneas de fuentes, evidencias e informes versionados; revisión cuando cambian las condiciones. |
| Aprobaciones y uso | Aprobación por acción, revisión de efectos inciertos y límites de gasto persistentes compartidos. |

## Primeros pasos

1. Descarga y ejecuta el [instalador Windows x64 sin firma](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe), o compila desde el código como se indica abajo. El paquete incluye Python y Chromium.
2. Abre **Connections** desde la configuración o el selector de modelo. Elige DeepSeek, `https://api.deepseek.com` y `deepseek-flash`; guarda tu clave en el almacén local cifrado.
3. Configura los límites diario y por tarea en `[assistant_usage]` del archivo local `%APPDATA%\nanoMuse Assistant\assistant.toml`. Empiezan en cero; selecciona la política revisada y una cantidad que cubra las reservas. Consulta la [guía de uso](../../docs/assistant.md#local-runtime-usage-limits). No guardes claves allí.
4. Elige **Runtime → Use configured model**, o sal desde la bandeja e inicia `nanoMuse-Assistant.exe --live`. Guardar una clave no cambia el modo ni concede presupuesto.
5. Describe el objetivo, revisa el plan y cada acción, y sigue su progreso en **Task ledger**. Puedes pausar o tomar el control.

> Los perfiles nuevos usan respuestas de guion sin conexión, claramente identificadas. No son un modelo de lenguaje local.

## Ejemplos de solicitudes

- Organiza tres objetivos de aprendizaje para esta semana y una lista de revisión.
- Resume mis notas Markdown del espacio de trabajo con referencias.
- Propón una organización de archivos y espera mi confirmación.
- Compara dos repositorios según mis requisitos y conserva las evidencias.

## Compilar en Windows

Requiere Windows x64, Git, Node.js 22, Python 3.13 y uv. Ejecuta desde la raíz. El script prepara las dependencias JavaScript, Chromium y el entorno Python.

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

La aplicación se genera en `desktop/assistant/dist/win-unpacked/` y el instalador en `desktop/assistant/dist/`. Usa `-Target dir` para el formato sin instalador. Los instaladores locales no están firmados. Consulta la [guía de escritorio](../../docs/assistant-desktop.md).

## Datos y ejecución

- Electron abre un servicio Python local. Conversaciones, tareas, recordatorios y versiones de investigación se guardan en archivos y SQLite.
- El contexto necesario se envía al proveedor del modelo elegido. El almacenamiento local no hace que las solicitudes a modelos en la nube permanezcan en el equipo.
- Cerrar la ventana mantiene la aplicación en la bandeja. Salir detiene su servicio. Selecciona un perfil de asistente existente con `--profile="D:\AssistantData\profile"`; no se importan automáticamente directorios exclusivos de investigación.

## Alcance actual

- Esta rama se centra en Windows x64; conserva el código móvil, relay y Harness del proyecto original.
- El perfil aislado admite adaptadores de texto DeepSeek revisados y acciones deterministas de Windows. No activa dictado, control visual por modelos ni memoria con embeddings.
- Los recordatorios necesitan el proceso abierto y el equipo despierto. La recuperación de la nueva arquitectura tras suspensión o bloqueo no se presenta como verificada.

## Documentación

- [Uso de escritorio, compilación y datos existentes](../../docs/assistant-desktop.md)
- [Planes, aprobaciones y límites de uso](../../docs/assistant.md)
- [Arquitectura](../../docs/architecture.md)
- [Avisos del proyecto original y terceros](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## Licencia y atribución

Basado en nanoMuse, bajo **GPL-3.0-or-later**. Se conservan los avisos en [LICENSE](../../LICENSE), [NOTICE](../../NOTICE) y [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). El escritorio DeepSeek Harness sigue siendo una entrada independiente. Normas: [CONTRIBUTING.md](../../CONTRIBUTING.md).

<details>
<summary>Colaboradores de la comunidad original</summary>

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
