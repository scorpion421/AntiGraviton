# Central database rules directive



All system rules, coding standards, administrative credentials, and skills are maintained in the central database:

`~/.gemini/database/customizations.db`



The agent must always adhere to the active global rules registered in the database with HIGHEST PRIORITY:



1. **Global output formatting invariant (strict enforcement across all operations)**:

   - **No em-dashes or en-dashes**: Under no circumstances output em-dashes (Unicode U+2014) or en-dashes (Unicode U+2013). This applies universally to ALL output: chat responses, markdown documents, code comments, commit messages, and artifacts. Always use standard ASCII hyphens (`-`), colons (`:`), or parentheses instead.

   - **No emojis**: Under no circumstances output emojis, smileys, or decorative Unicode symbols.

   - **Sentence case for headings and labels**: Always use natural sentence case for headings, subheadings, and bold bullet labels (only capitalize the first word and proper nouns/acronyms like `GitHub`, `Gitea`, `SkillsDB`, `SQLite`, `JSONL`, `UTF-8`, `API`, `CLI`, `IDE`). Never use newspaper-style Title Case.



2. **Conversational communication and writing standards**:

   - Always use informal German ("Du" / "Dein" when addressing the user, and "wir" / "unser" when referring to our joint work; strictly NEVER use formal "Sie" and NEVER use second-person plural "Ihr" / "Euer" / "Eurer" / "Euch", because there are only the two of us: the user and you).

   - Use natural German spelling INCLUDING umlauts (ä, ö, ü, ß). Never replace umlauts with ae, oe, ue in regular conversation text.

   - **German compound nouns (strictly no superfluous hyphens / Deppenbindestriche)**: Strictly never use superfluous or incorrect hyphens in German compound words ("Deppenbindestrich"). Always write German compound nouns (Komposita) as a single joined word (e.g., "Abteilungskontingent" never "Abteilungs-Kontingent", "Browsertool" never "Browser-Tool", "Tabwechsel" never "Tab-Wechsel", "Standardeinstellung" never "Standard-Einstellung", "Kontextkompaktierung" never "Kontext-Kompaktierung", "Subagentenisolation" never "Subagenten-Isolation", "Tokenreduktion" never "Token-Reduktion", "Tokenökonomie" never "Token-Ökonomie"), unless an acronym or official orthographic standard specifically mandates a hyphen (e.g., "IT-Leiter", "B2B-Bereich").



3. **Coding and scripting standards (strict language separation, SemVer 2.0.0, and Keep a Changelog 1.1.0)**:

   - **Language boundary (zero exceptions)**: Even though conversational chat with the user is strictly in informal German ("Du"), ALL code, comments, console outputs, logs, UI labels, buttons, dialogs, user-facing error strings, and commit messages MUST BE IN GRAMMATICALLY CORRECT ENGLISH (ASCII only). NEVER translate UI dialogs, labels, or buttons to German, even when conversing in German!

   - **Strict Semantic Versioning (SemVer 2.0.0) and Keep a Changelog 1.1.0**: Always version projects, packages, changelogs, and releases strictly according to https://semver.org/ (`MAJOR.MINOR.PATCH`) and https://keepachangelog.com/en/1.1.0/. Never overwrite an already published release version in place; always increment the version (`MAJOR`, `MINOR`, or `PATCH`), document changes in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` using exclusively official Keep a Changelog categories (`### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, `### Security`), and publish matching Git tags and formal Releases (locally in Git, and on Gitea/GitHub when configured via `skillsdb gitea-sync --releases --github`).

   - **Deliverable release artifacts (APKs, binaries, installers, packages)**: Whenever a project produces compiled or packaged deliverable artifacts as part of its purpose (`.apk`, `.aab`, `.exe`, `.msi`, `.xpi`, `.crx`, `.vsix`, `.whl`, `.jar`, `.dmg`, `.deb`, `.rpm`, `.appimage`, or release archives in `releases/` or `dist/`), every formal SemVer Release on Gitea (and GitHub when synced) must include and attach those built output artifacts as downloadable Release Assets (`_attach_release_binary_assets`).

   - **Canonical SkillsDB source discipline**: Never patch `~/.gemini/database/db_manager.py` exclusively in-place; always edit the modular `skillsdb/` source in `AntiGraviton`, compile via `python build.py`, and deploy via `python deploy.py`.

   - **Commit authorship**: Whenever you author or commit code changes via Git or Gitea, always set the commit author and committer to `Antigravity <antigravity@local.dev>` (`git -c user.name="Antigravity" -c user.email="antigravity@local.dev" commit ...`).

   - Strictly no VBScript (`.vbs`, `cscript`, `wscript`). Always use native PowerShell or standard APIs.

   - **Standardized 9-block `README.md` blueprint (`standard-readme` + Shields.io badges + Table of contents)**: Every project `README.md` must follow the 9-block structure: (1) `# Title`, (2) clickable Shields.io badge header (`Version` -> `CHANGELOG.md`, `Tests` -> `tests/`, tech stack, `License` -> `LICENSE` defaulting to `AGPLv3 + Commons Clause`), (3) short 1-3 sentence description, (4) `## Table of contents` with clickable Markdown anchors (`[Section](#section-anchor)`), (5) executive summary & measured impact table, (6) installation & quickstart, (7) usage & commands, (8) architecture & ASCII repository layout (`|--`) with test verification, and (9) SemVer 2.0.0 versioning policy & license.



4. **Admin elevation and 100% transparent IT audit standard (`C:\IT_ADMIN_LOGS\<USERNAME>\<TASK>\<TARGET>\<Date>\<hh_mm_ss>\`)**:
   - Execute administrative tasks directly if the active session is already elevated; otherwise use optional DPAPI credentials (`~/.gemini/config/credentials/*.credential.xml` for local admin or `.adm` accounts) without UAC prompts, or fall back to standard Windows UAC elevation (`Start-Process -Verb RunAs`).
   - **SOC-compliant transparent administration (strictly no Blackhat / `Temp` patterns and strictly 100% live runtime values - never hardcoded names or hosts)**: Never stage scripts in `C:\Windows\Temp`, never use cryptic file or task names, never use `cmd.exe /c` wrappers, never create remote services via `sc.exe \\`, and NEVER hardcode static usernames, workstation names, or domain strings. Whenever an administrative script or scheduled task is executed on a server or client (after explicit user approval):
     1. Use the structured hierarchy `C:\IT_ADMIN_LOGS\<USERNAME>\<TASK>\<TARGET>\<Date>\<hh_mm_ss>\` (queried 100% live at runtime via `$env:USERNAME`, `(Get-Date).ToString('yyyy-MM-dd')`, and `(Get-Date).ToString('HH_mm_ss')`) and task folder `\IT_ADMIN_LOGS\<USERNAME>\` with a descriptive `-Description`.
     2. Write a persistent `AuditLog_<Target>_<Date>_<hh_mm_ss>.log` (and matching script header) where all identity and environment fields are queried 100% live at runtime (never static): `Timestamp (Local)` and `Timestamp (UTC)` (to the second), `Origin Workstation` (queried live via `[System.Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName` / `$env:COMPUTERNAME`), `Interactive User` (queried live via `[System.Security.Principal.WindowsIdentity]::GetCurrent().Name` / `"$env:USERDOMAIN\$env:USERNAME"`), `Admin Account` (queried live from `$cred.UserName` or the elevated identity), `Execution Context`, `Target System` and `Target Object`, the full hop-by-hop `Execution Path`, `Purpose`, `[1/4 PRE-CHECK]` state before modification, `[2/4..3/4 ACTION]` steps executed, `[4/4 POST-CHECK]` verified state after modification, and `[CLEANUP]` timestamp (removing the temporary Scheduled Task and `.ps1` script while retaining the `AuditLog` file).



5. **Turn-economy, diagnostic protocol, and context hygiene**:

   - **Anti-salami-slicing (`read_file` budget)**: Read files `<= 800 lines` completely in a single call when inspecting or editing them. For files `> 800 lines`, locate target line numbers via grep/script first and execute all `read_file` calls in parallel within a single turn. Never slice a file sequentially across multiple 80-120 line turns.

   - **Subagent firewall (`invoke_subagent`)**: Execute targeted edits in 1-3 known files and targeted single-host / remote WMI / CIM / system administration commands (`Invoke-Command`, `Get-CimInstance`, DPAPI/WinRM/SMB host diagnostics) directly in the main context. For open-ended local codebase root-cause debugging (`>= 4 search steps`) or scanning `> 5 local files`, delegate exploration to a `research` subagent (which must return exact file paths, line numbers, and root cause) to keep the main context free of search noise.

   - **Cluster-fix and chained pipelines**: When fixing a bug, search the entire project for all occurrences of that bug class before rebuilding, and chain build, test, and verification into a single command (`build && test && verify`).

   - **Zero-polling and local indexing**: Set `WaitMsBeforeAsync` to `5000-10000` for fast commands; when a command backgrounds, never poll `manage_task status` (rely on automatic `Reactive Wakeup`). For codebases `> 300 files` requiring `>= 3` analysis rounds, create a local SQLite index in `scratch/` instead of repeated file-tree scans.

   - **Tooling anomaly note at end of task (no recurring silent workarounds)**: If an internal AntiGraviton or SkillsDB tool, hook, or Gitea/GitHub sync command exhibits a bug or unexpected failure that forces you to apply a manual workaround in the background, complete the user's task first without interrupting the flow, and append a brief note at the end of your final response suggesting a root-cause fix in AntiGraviton so you do not have to work around the same tooling bug repeatedly.



6. **Autonomous project memory, Gitea sync, and knowledge protocol (zero user effort)**:

   - In project workspaces, `.agents/memory.db` serves as the unshakeable single source of truth across all turns.

   - **Mandatory fact, offset, and task indexing in `.agents/memory.db` (zero re-reading after compaction)**: Whenever you analyze a codebase, reverse-engineer binaries/Smali, or execute multi-step tasks, immediately persist discovered file structures, class/method mappings, line offsets, and build parameters via `skillsdb mem-save-fact "<key>" "<value>" --project "<dir>"` and track multi-step work items via `skillsdb mem-task-add "<title>" --project "<dir>"` (updating via `mem-task-update`). Always anchor structural findings in `.agents/memory.db` so context compaction never forces you to re-read the same files repeatedly.

   - **Autonomous skill routing**: When tackling technical domain tasks (Flutter, BigQuery, Docker, Android, Data Science, etc.), autonomously query `skillsdb suggest "<task>"` and inspect micro-skills via `skillsdb get-skill <name> --section "<Section>"` or `--summary` without asking the user.

   - **Autonomous snapshotting**: Silently record milestone snapshots via `skillsdb mem-save-snapshot "<summary>"` upon completing significant changes, refactors, or feature milestones.

   - **Optional local Gitea repository upload, sync, and Keep a Changelog releases (SkillsDB works 100% standalone without Gitea)**: SkillsDB and `.agents/memory.db` operate 100% autonomously with local Git even if Gitea is not installed (`-SkipGiteaService` / `--skip-gitea-service` / `gitea-config --disable`). When local Gitea is configured or requested by the user, immediately use the built-in SkillsDB Gitea CLI without manual token hunting:

     - Initialize and push any directory/project to local Gitea: `skillsdb gitea-init "<target_directory>"`

     - Commit, push changes, and auto-sync Keep a Changelog releases to Gitea (and GitHub): `skillsdb gitea-sync -m "<commit message>" --project "<target_directory>" [--releases] [--github]`

     - **Unversioned upload confirmation guard**: If the user requests uploading or pushing changes without incrementing the SemVer version, always ask once for explicit confirmation first ("Soll ich das wirklich ohne Versionierung hochladen?"), and only after the user confirms execute `skillsdb gitea-sync -m "<commit message>" --project "<target_directory>" --allow-unversioned [--github]`.

     - Publish or update a specific Gitea (and optional GitHub) release with Git tag and `CHANGELOG.md` notes: `skillsdb gitea-release [vX.Y.Z] --title "<Title>" --project "<target_directory>" [--github]`

     - Check Gitea service, API health, and repositories: `skillsdb gitea-status`

     - **Gitea repository collision guard**: If `skillsdb gitea-init` or `skillsdb gitea-sync` outputs `[GITEA REPOSITORY COLLISION]`, if Git telemetry shows `[GITEA COLLISION ALERT]`, or if the user assumes a project does not exist on Gitea yet while a repository with that name is already present on Gitea, immediately pause and ask the user in informal German how to proceed before pushing: reuse the existing repository (`--use-existing`), create it under a new name (`--name <NewName>`), or overwrite it (`--overwrite`).

     - Always format Gitea (`http://localhost:3000/...`) and GitHub URLs as clickable Markdown links `[Label](url)`.

   - **Autonomous learning**: When the user expresses lasting preferences, corrections, or conventions, silently persist them via `skillsdb mem-save-decision` (workspace) or `skillsdb learn-rule` (global).

   - **Continuous flow**: Never interrupt the user's flow or prompt the user to restart/switch chats; maintain precision and stability across long sessions through surgical micro-skills and memory anchors.



7. **Adaptive model concurrency and multi-pool quota routing protocol (Ultra / Pro / Lite)**:

   - SkillsDB autonomously detects both the account subscription tier (`ultra`, `pro`, `lite`), the active Antigravity model family across all internal quota pools (`Gemini 4 Argon [Fast | Medium | High]`, `Gemini 3.8 Flash [Fast | Medium | High]`, `Claude Opus 5.5 [Fast | Medium | High]`, `Claude Sonnet 5.5 [Fast | Medium | High]`, and `GPT-OSS 120B [Medium]`), and external Gemini offload pools (`@google/gemini-cli` OAuth, `Google AI Studio` REST API, and `gemini.google.com` Web handover).

   - **Ultra mode (`Gemini 4 Argon [Medium/High]` / `Claude Opus 5.5 [Medium/High]`)**: Utilize high-concurrency capabilities (up to 16 parallel reader threads via `skillsdb get-skills`, `skillsdb get-cluster`, and `skillsdb search-multi`). Keep the flagship main context clean by offloading read-only log/diff/multi-file analysis exceeding the tier threshold (`> 400 lines` on `Fast/Medium` or `> 250 lines` on `High` effort) via `skillsdb gemini-digest --files <paths> --query "<question>"` and routing exploratory `research` subagents to `Model="flash"` (`Gemini 3.8 Flash`).

   - **Pro / Lite mode (`Claude Sonnet 5.5`, `Gemini 3.8 Flash`, `GPT-OSS 120B`, or `shield` conservation mode)**: Conserve rolling 5-hour quota and avoid cumulative context tax. Fetch targeted micro-skill sections via `skillsdb get-skill <name> --section "<Section>"` or `--summary`, offload read-only file/log analysis (`> 200 lines` on `Pro`, `> 80 lines` on `Lite`) via `skillsdb gemini-digest`, and ensure subagents never consume scarce `Claude 5.5` quota by routing subagents to `Model="flash"` or `"flash_lite"` (enforced automatically at 0 extra turns by the `PreToolUse` hook).



To inspect or search detailed rules and workflow skills on demand, use the `customizations-db` skill or run:

`skillsdb get-active-rules` (or `python "$env:USERPROFILE\.gemini\database\db_manager.py" get-active-rules`)



