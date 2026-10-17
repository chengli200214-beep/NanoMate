# nanoMuse Assistant

Un assistant personnel Windows avec un registre de tâches et un module de recherche fondé sur des preuves.

[English](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

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

**nanoMuse Assistant** réunit conversations, plans, rappels, documents et recherche dans une application de bureau. Décrivez un objectif, relisez les étapes puis suivez les résultats dans un registre persistant. Cette adaptation de [nanoMuse](https://github.com/nano-muse/nanoMuse) ajoute un espace Windows indépendant tout en conservant le module de recherche.

## Aperçus du produit

Captures de la prévisualisation actuelle du bureau, avec un profil hors ligne isolé. Tâches, dépôts et rapports sont des exemples synthétiques, pas des résultats d’évaluation. Ouvrez une image pour la voir en entier.

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="Accueil et conversations" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="Registre de tâches et validation humaine"></a><br><b>Registre de tâches et validation humaine</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="Conditions et rapport de recherche"></a><br><b>Conditions et rapport de recherche</b></td>
  </tr>
</table>

## Fonctionnalités

| Domaine | Fonction |
| --- | --- |
| Conversations | Modèles de texte DeepSeek, conversations multiples, recherche par titre et thèmes clair et sombre. |
| Registre de tâches | Objectifs, étapes, versions du plan, exécutions et résultats conservés après redémarrage. |
| Plans et rappels | Création ou modification de rappels et de tâches périodiques avec journal des changements. |
| Documents | Lecture de Markdown local et productions liées aux sources avec vérification du contenu. |
| Préférences | Préférences explicitement confirmées, avec expiration et suppression. |
| Actions encadrées | Prévisualisation des opérations sur fichiers, observation du navigateur et actions Windows bornées, avec pause et reprise manuelle. |
| Recherche | Comparaisons avec instantanés des sources, preuves et rapports versionnés, réexaminés après modification des conditions. |
| Autorisations et usage | Validation par action, conservation des effets incertains pour examen et plafonds de dépenses persistants partagés. |

## Prise en main

1. Téléchargez et lancez le [programme Windows x64 non signé](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe), ou compilez les sources ci-dessous. Python et Chromium sont inclus.
2. Ouvrez **Connections** depuis les réglages ou le sélecteur de modèle. Choisissez DeepSeek, `https://api.deepseek.com` et `deepseek-flash`, puis enregistrez votre clé dans le coffre local chiffré.
3. Réglez les plafonds quotidien et par tâche dans `[assistant_usage]` du fichier local `%APPDATA%\nanoMuse Assistant\assistant.toml`. Ils commencent à zéro. Choisissez la politique révisée et un montant couvrant les réservations, voir le [guide](../../docs/assistant.md#local-runtime-usage-limits). N’y mettez aucune clé.
4. Choisissez **Runtime → Use configured model**, ou quittez depuis la zone de notification puis lancez `nanoMuse-Assistant.exe --live`. Enregistrer une clé ne change ni le mode ni le budget.
5. Décrivez votre objectif, validez le plan et les actions, puis suivez **Task ledger**. Vous pouvez mettre en pause ou reprendre la main.

> Les nouveaux profils utilisent des réponses de script hors ligne clairement signalées. Ce ne sont pas les réponses d’un modèle de langage local.

## Exemples de demandes

- Organise trois objectifs d’apprentissage pour cette semaine et une liste de bilan.
- Résume les notes Markdown de mon espace de travail en citant les sources.
- Prépare un plan de classement des fichiers et attends ma confirmation.
- Compare deux dépôts selon mes critères et conserve les preuves.

## Compiler sous Windows

Prérequis : Windows x64, Git, Node.js 22, Python 3.13 et uv. Exécutez depuis la racine du dépôt. Le script prépare les dépendances JavaScript, Chromium et Python.

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

Application dans `desktop/assistant/dist/win-unpacked/`, installateur dans `desktop/assistant/dist/`. `-Target dir` produit uniquement le répertoire décompressé. Les installateurs locaux ne sont pas signés. Voir le [guide de bureau](../../docs/assistant-desktop.md).

## Données et exécution

- Electron ouvre un service Python local. Conversations, tâches, rappels et versions de recherche restent dans des fichiers et des bases SQLite locaux.
- Le contexte nécessaire est envoyé au fournisseur du modèle choisi. Le stockage local ne signifie pas que les appels au modèle restent sur cet ordinateur.
- Fermer la fenêtre conserve l’application dans la zone de notification. Quitter arrête son service. Pour un profil existant, utilisez `--profile="D:\AssistantData\profile"` ; les anciens répertoires de recherche ne sont pas importés automatiquement.

## Périmètre actuel

- Cette branche cible Windows x64 et conserve le code mobile, relay et Harness du projet original.
- Le profil isolé active les adaptateurs de texte DeepSeek révisés et les actions Windows déterministes, sans dictée, contrôle par modèle visuel ni mémoire par embeddings.
- Les rappels exigent un processus actif et un ordinateur éveillé. La reprise de la nouvelle architecture après veille ou verrouillage n’est pas déclarée vérifiée.

## Documentation

- [Utilisation du bureau, compilation et données](../../docs/assistant-desktop.md)
- [Plans, autorisations et limites](../../docs/assistant.md)
- [Architecture](../../docs/architecture.md)
- [Mentions du projet original et des tiers](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## Licence et attribution

Fondé sur nanoMuse sous **GPL-3.0-or-later**. Les mentions restent dans [LICENSE](../../LICENSE), [NOTICE](../../NOTICE) et [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md). Le bureau DeepSeek Harness reste une entrée distincte. Règles : [CONTRIBUTING.md](../../CONTRIBUTING.md).

<details>
<summary>Contributeurs de la communauté d’origine</summary>

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
