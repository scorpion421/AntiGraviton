# Central database rules directive

All system rules, coding standards, administrative credentials, and skills are maintained in `~/.gemini/database/customizations.db`. Adhere to these active global rules with HIGHEST PRIORITY:

1. **Global output formatting invariant (strict enforcement across all operations)**:
   - **No em-dashes or en-dashes**: Never output em-dashes (U+2014) or en-dashes (U+2013) in chat, markdown, code comments, commit messages, or artifacts. Use standard ASCII hyphens (`-`), colons (`:`), or parentheses.
   - **No emojis**: Never output emojis, smileys, or decorative Unicode symbols.
   - **Sentence case for headings and labels**: Use natural sentence case for headings, subheadings, and bold bullet labels (capitalize only the first word and proper nouns/acronyms like `GitHub`, `Gitea`, `SkillsDB`, `SQLite`, `JSONL`, `UTF-8`, `API`, `CLI`, `IDE`). Never use Title Case.

2. **Conversational communication and writing standards**:
   - Always use informal German ("Du" / "Dein" for the user, "wir" / "unser" for joint work; strictly NEVER formal "Sie" or plural "Ihr" / "Euer" / "Euch").
   - Use natural German spelling including umlauts (ä, ö, ü, ß).
   - **German compound nouns (strictly no Deppenbindestriche)**: Always write German compound nouns (Komposita) as a single joined word (e.g., "Abteilungskontingent" never "Abteilungs-Kontingent", "Browsertool" never "Browser-Tool", "Tabwechsel" never "Tab-Wechsel", "Standardeinstellung" never "Standard-Einstellung", "Kontextkompaktierung" never "Kontext-Kompaktierung", "Subagentenisolation" never "Subagenten-Isolation", "Tokenreduktion" never "Token-Reduktion", "Tokenökonomie" never "Token-Ökonomie").

3. **Coding and scripting standards (strict language separation, SemVer 2.0.0, and Keep a Changelog 1.1.0)**:
   - **Language boundary (zero exceptions)**: All code, comments, console outputs, logs, UI labels, buttons, dialogs, error strings, and commit messages MUST be in grammatically correct English (ASCII only). Never translate UI strings to German.
   - **Strict Semantic Versioning (SemVer 2.0.0) and Keep a Changelog 1.1.0**: Version projects according to https://semver.org/ (`MAJOR.MINOR.PATCH`) and https://keepachangelog.com/en/1.1.0/. Never overwrite an already published release version in place; increment the version, document changes in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` (`### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, `### Security`), and publish matching Git tags and Releases (`skillsdb gitea-sync --releases --github`). Attach built deliverable artifacts (`.apk`, `.aab`, `.exe`, `.msi`, `.xpi`, `.vsix`, `.whl`, `.jar`, `.zip`) via `_attach_release_binary_assets`.
   - **Canonical SkillsDB source & authorship**: Edit modular `skillsdb/` source in `AntiGraviton`, compile via `python build.py`, and deploy via `python deploy.py`. Author commits as `Antigravity <antigravity@local.dev>`. Strictly no VBScript (`.vbs`, `cscript`, `wscript`).
   - **Standardized 9-block `README.md` blueprint**: (1) `# Title`, (2) clickable Shields.io badges (`Version` -> `CHANGELOG.md`, `Tests` -> `tests/`, tech stack, `License` -> `LICENSE` `AGPLv3 + Commons Clause`), (3) 1-3 sentence summary, (4) `## Table of contents`, (5) executive summary & impact table, (6) installation & quickstart, (7) usage & commands, (8) architecture & ASCII tree (`|--`), (9) SemVer 2.0.0 & license.

4. **Admin elevation and 100% transparent IT audit standard (`C:\IT_ADMIN_LOGS\<USERNAME>\<TASK>\<TARGET>\<Date>\<hh_mm_ss>\`)**:
   - Execute admin tasks directly if elevated, via optional DPAPI credentials (`~/.gemini/config/credentials/*.credential.xml`), or `Start-Process -Verb RunAs`.
   - **SOC-compliant transparent administration**: Never stage in `C:\Windows\Temp`, never use cryptic names or `cmd.exe /c` wrappers, never create remote services via `sc.exe \\`, and never hardcode usernames, hosts, or domains. After user approval, use `C:\IT_ADMIN_LOGS\<USERNAME>\<TASK>\<TARGET>\<Date>\<hh_mm_ss>\` and task folder `\IT_ADMIN_LOGS\<USERNAME>\` with a live-queried `AuditLog_<Target>_<Date>_<hh_mm_ss>.log` (`[1/4 PRE-CHECK]`, `[2/4..3/4 ACTION]`, `[4/4 POST-CHECK]`, `[CLEANUP]`).

5. **Turn-economy, diagnostic protocol, and context hygiene**:
   - **Anti-salami-slicing & symbol lookup**: Read files `<= 800 lines` in a single `read_file` call. For files `> 800 lines`, locate exact symbol line ranges first via `skillsdb locate <symbol> --project "<dir>"` (or grep) and batch `read_file` calls in parallel.
   - **Subagent firewall**: Run targeted edits (1-3 files) and host/WMI/CIM admin commands directly in main context. Delegate open-ended codebase exploration (`>= 4` searches or `> 5` files) to a `research` subagent (`Model="flash"`).
   - **Cluster-fix, batching, and zero-polling**: Search for all occurrences of a bug class before rebuilding, chain `build && test && verify`, use `skillsdb mem-batch` to combine memory writes in one process, and set `WaitMsBeforeAsync=10000` (never poll `manage_task status`). Append a brief note at the end of your response if an internal tool needed a workaround.

6. **Autonomous project memory, Gitea sync, and knowledge protocol (pure Pull)**:
   - Persist facts/offsets/tasks in `.agents/memory.db` via `skillsdb mem-batch --fact "k=v" --task-add "<title>" --snapshot "<summary>" --project "<dir>"` (or `mem-save-fact`, `mem-task-add`, `mem-task-update`, `mem-save-decision`, `learn-rule`) and locate symbols via `skillsdb locate <symbol>`. Query domain micro-skills on demand via `skillsdb suggest "<task>"` and `skillsdb get-skill <name> --section "<Section>"`.
   - **Gitea & GitHub sync**: Use `skillsdb gitea-init "<dir>"`, `skillsdb gitea-sync -m "<msg>" --project "<dir>" [--releases] [--github]`, `skillsdb gitea-release [vX.Y.Z] --project "<dir>" [--github]`, and `skillsdb gitea-status`. Ask confirmation before `--allow-unversioned` ("Soll ich das wirklich ohne Versionierung hochladen?") and pause on `[GITEA REPOSITORY COLLISION]` (`--use-existing`, `--name`, or `--overwrite`). Format Gitea/GitHub URLs as clickable links `[Label](url)`.

7. **Model & concurrency catalog (pure Pull, zero background control plane)**:
   - Supports `ultra` (16 parallel reader threads via `get-skills`, `get-cluster`, `search-multi`) and `pro` (8 threads) across `Gemini 4 Argon`, `Gemini 3.8 Flash`, `Claude Opus 5.5`, `Claude Sonnet 5.5`, and `GPT-OSS 120B`. Keep 100% of code generation on `Gemini 4 Argon` or `Claude 5.5`; route only read-only `research` subagents to `Model="flash"`.
