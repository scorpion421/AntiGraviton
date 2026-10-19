# Central database rules directive

All system rules, coding standards, administrative credentials, and skills are maintained in the central database:
`~/.gemini/database/customizations.db`

The agent must always adhere to the active global rules registered in the database with HIGHEST PRIORITY:

1. **Global output formatting invariant (strict enforcement across all operations)**:
   - **No em-dashes or en-dashes**: Under no circumstances output em-dashes (Unicode U+2014) or en-dashes (Unicode U+2013). This applies universally to ALL output: chat responses, markdown documents, code comments, commit messages, and artifacts. Always use standard ASCII hyphens (`-`), colons (`:`), or parentheses instead.
   - **No emojis**: Under no circumstances output emojis, smileys, or decorative Unicode symbols.
   - **Sentence case for headings and labels**: Always use natural sentence case for headings, subheadings, and bold bullet labels (only capitalize the first word and proper nouns/acronyms like `GitHub`, `Gitea`, `SkillsDB`, `SQLite`, `JSONL`, `UTF-8`, `API`, `CLI`, `IDE`). Never use newspaper-style Title Case.

2. **Conversational communication and writing standards**:
   - Always use informal German ("Du", never "Sie") when conversing with the user.
   - Use natural German spelling INCLUDING umlauts (ä, ö, ü, ß). Never replace umlauts with ae, oe, ue in regular conversation text.
   - **Schreibregeln für Komposita (strictly no Deppenbindestriche)**: Strictly never use superfluous or incorrect hyphens in German compound words ("Deppenbindestrich"). Always write German compound nouns (Komposita) as a single joined word (e.g., "Abteilungskontingent" never "Abteilungs-Kontingent", "Browsertool" never "Browser-Tool", "Tabwechsel" never "Tab-Wechsel", "Standardeinstellung" never "Standard-Einstellung", "Kontextkompaktierung" never "Kontext-Kompaktierung", "Subagentenisolation" never "Subagenten-Isolation", "Tokenreduktion" never "Token-Reduktion", "Tokenökonomie" never "Token-Ökonomie"), unless an acronym or official orthographic standard specifically mandates a hyphen (e.g., "IT-Leiter", "B2B-Bereich").

3. **Coding and scripting standards (strict language separation, SemVer 2.0.0, and Keep a Changelog 1.1.0)**:
   - **Language boundary (zero exceptions)**: Even though conversational chat with the user is strictly in informal German ("Du"), ALL code, comments, console outputs, logs, UI labels, buttons, dialogs, user-facing error strings, and commit messages MUST BE IN GRAMMATICALLY CORRECT ENGLISH (ASCII only). NEVER translate UI dialogs, labels, or buttons to German, even when conversing in German!
   - **Strict Semantic Versioning (SemVer 2.0.0) and Keep a Changelog 1.1.0**: Always version projects, packages, changelogs, and releases strictly according to https://semver.org/ (`MAJOR.MINOR.PATCH`) and https://keepachangelog.com/en/1.1.0/. Never overwrite an already published release version in place; always increment the version (`MAJOR`, `MINOR`, or `PATCH`), document changes in `CHANGELOG.md` under `## [X.Y.Z] - YYYY-MM-DD` using exclusively official Keep a Changelog categories (`### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, `### Security`), and publish matching Git tags and formal Releases on both Gitea and GitHub (`skillsdb gitea-sync --releases --github`).
   - **Commit authorship**: Whenever you author or commit code changes via Git or Gitea, always set the commit author and committer to `Antigravity <antigravity@local.dev>` (`git -c user.name="Antigravity" -c user.email="antigravity@local.dev" commit ...`).
   - Strictly no VBScript (`.vbs`, `cscript`, `wscript`). Always use native PowerShell or standard APIs.

4. **Admin elevation (local admin default + optional DPAPI)**:
   - Execute administrative tasks directly if the active session is already elevated; otherwise use optional DPAPI credentials (`~/.gemini/config/credentials/*.credential.xml` for local admin or `.adm` accounts) without UAC prompts, or fall back to standard Windows UAC elevation (`Start-Process -Verb RunAs`).

5. **Token efficiency and context hygiene**:
   - Strict context hygiene (slice file reads with StartLine/EndLine, compact command outputs like `git status -s`, subagent isolation, no verbose fluff or restatements).

6. **Autonomous project memory, Gitea sync, and knowledge protocol (zero user effort)**:
   - In project workspaces, `.agents/memory.db` serves as the unshakeable single source of truth across all turns.
   - **Autonomous skill routing**: When tackling technical domain tasks (Flutter, BigQuery, Docker, Android, Data Science, etc.), autonomously query `skillsdb suggest "<task>"` and inspect micro-skills via `skillsdb get-skill <name> --section "<Section>"` or `--summary` without asking the user.
   - **Autonomous snapshotting**: Silently record milestone snapshots via `skillsdb mem-save-snapshot "<summary>"` upon completing significant changes, refactors, or feature milestones.
   - **Local Gitea repository upload, sync, and Keep a Changelog releases**: Whenever the user asks to upload, back up, or push any directory or project to Gitea (or to check Gitea status), immediately use the built-in SkillsDB Gitea CLI without manual token hunting:
     - Initialize and push any directory/project to local Gitea: `skillsdb gitea-init "<target_directory>"`
     - Commit, push changes, and auto-sync Keep a Changelog releases to Gitea (and GitHub): `skillsdb gitea-sync -m "<commit message>" --project "<target_directory>" [--releases] [--github]`
     - Check Gitea service, API health, and repositories: `skillsdb gitea-status`
     - Always format Gitea (`http://localhost:3000/...`) and GitHub URLs as clickable Markdown links `[Label](url)`.
   - **Autonomous learning**: When the user expresses lasting preferences, corrections, or conventions, silently persist them via `skillsdb mem-save-decision` (workspace) or `skillsdb learn-rule` (global).
   - **Continuous flow**: Never interrupt the user's flow or prompt the user to restart/switch chats; maintain precision and stability across long sessions through surgical micro-skills and memory anchors.

7. **Adaptive model concurrency protocol (Ultra / Standard / Lean)**:
   - SkillsDB autonomously detects the active Gemini model tier (`ultra`, `standard`, `lean`).
   - **Gemini Ultra mode**: When Ultra is active (including `Gemini 4 Argon` and `Gemini 3.1 Pro`), utilize high-concurrency capabilities. Fetch skills in parallel batch calls (`skillsdb get-skills <skill1> <skill2> ...` or `skillsdb get-cluster <domain>`), execute multi-query searches concurrently (`skillsdb search-multi "<q1>" "<q2>"`), and swarm tasks across parallel background subagents (up to 16 workers) to minimize turnaround latency.
   - **Gemini Standard / Lean mode**: Conserve quota and avoid prompt bloat. Fetch targeted micro-skill sections via `skillsdb get-skill <name> --section "<Section>"` or `--summary` sequentially.

To inspect or search detailed rules and workflow skills on demand, use the `customizations-db` skill or run:
`skillsdb get-active-rules` (or `python "$env:USERPROFILE\.gemini\database\db_manager.py" get-active-rules`)

