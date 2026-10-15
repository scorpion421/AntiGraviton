# Central Database Rules Directive

All system rules, coding standards, administrative credentials, and skills are maintained in the central database:
`~/.gemini/database/customizations.db`

The agent must always adhere to the active global rules registered in the database with HIGHEST PRIORITY:

1. **Global Output Formatting Invariant (Strict Enforcement Across ALL Operations)**:
   - **No Em-Dashes or En-Dashes**: Under no circumstances output em-dashes (Unicode U+2014) or en-dashes (Unicode U+2013). This applies universally to ALL output: chat responses, markdown documents, code comments, commit messages, and artifacts. Always use standard ASCII hyphens (`-`), colons (`:`), or parentheses instead.
   - **No Emojis**: Under no circumstances output emojis, smileys, or decorative Unicode symbols.

2. **Conversational Communication & Writing Standards**:
   - Always use informal German ("Du", never "Sie") when conversing with the user.
   - Use natural German spelling INCLUDING umlauts (ä, ö, ü, ß). Never replace umlauts with ae, oe, ue in regular conversation text.
   - **Schreibregeln für Komposita (Strictly No Deppenbindestriche)**: Strictly never use superfluous or incorrect hyphens in German compound words ("Deppenbindestrich"). Always write German compound nouns (Komposita) as a single joined word (e.g., "Abteilungskontingent" never "Abteilungs-Kontingent", "Browsertool" never "Browser-Tool", "Tabwechsel" never "Tab-Wechsel", "Standardeinstellung" never "Standard-Einstellung", "Kontextkompaktierung" never "Kontext-Kompaktierung", "Subagentenisolation" never "Subagenten-Isolation", "Tokenreduktion" never "Token-Reduktion", "Tokenökonomie" never "Token-Ökonomie"), unless an acronym or official orthographic standard specifically mandates a hyphen (e.g., "IT-Leiter", "B2B-Bereich").

3. **Coding & Scripting Standards (Strict Language Separation & SemVer 2.0.0)**:
   - **Language Boundary (Zero Exceptions)**: Even though conversational chat with the user is strictly in informal German ("Du"), ALL code, comments, console outputs, logs, UI labels, buttons, dialogs, user-facing error strings, and commit messages MUST BE IN GRAMMATICALLY CORRECT ENGLISH (ASCII only). NEVER translate UI dialogs, labels, or buttons to German, even when conversing in German!
   - **Strict Semantic Versioning (SemVer 2.0.0)**: Always version projects, packages, changelogs, and releases strictly according to https://semver.org/ (`MAJOR.MINOR.PATCH`). Never overwrite an already published release version in place; always increment the version (`MAJOR`, `MINOR`, or `PATCH`) and document changes in `CHANGELOG.md`.
   - Strictly no VBScript (`.vbs`, `cscript`, `wscript`). Always use native PowerShell or standard APIs.

4. **Admin Elevation (Local Admin Default + Optional DPAPI)**:
   - Execute administrative tasks directly if the active session is already elevated; otherwise use optional DPAPI credentials (`~/.gemini/config/credentials/*.credential.xml` for local admin or `.adm` accounts) without UAC prompts, or fall back to standard Windows UAC elevation (`Start-Process -Verb RunAs`).

5. **Token Efficiency & Context Hygiene**:
   - Strict context hygiene (slice file reads with StartLine/EndLine, compact command outputs like `git status -s`, subagent isolation, no verbose fluff or restatements).

6. **Autonomous Project Memory & Knowledge Protocol (Zero User Effort)**:
   - In project workspaces, `.agents/memory.db` serves as the unshakeable single source of truth across all turns.
   - **Autonomous Skill Routing**: When tackling technical domain tasks (Flutter, BigQuery, Docker, Android, Data Science, etc.), autonomously query `skillsdb suggest "<task>"` and inspect micro-skills via `skillsdb get-skill <name> --section "<Section>"` or `--summary` without asking the user.
   - **Autonomous Snapshotting**: Silently record milestone snapshots via `skillsdb mem-save-snapshot "<summary>"` upon completing significant changes, refactors, or feature milestones.
   - **Autonomous Learning**: When the user expresses lasting preferences, corrections, or conventions, silently persist them via `skillsdb mem-save-decision` (workspace) or `skillsdb learn-rule` (global).
   - **Continuous Flow**: Never interrupt the user's flow or prompt the user to restart/switch chats; maintain precision and stability across long sessions through surgical micro-skills and memory anchors.

7. **Adaptive Model Concurrency Protocol (Ultra / Standard / Lean)**:
   - SkillsDB autonomously detects the active Gemini model tier (`ultra`, `standard`, `lean`).
   - **Gemini Ultra Mode**: When Ultra is active, utilize high-concurrency capabilities. Fetch skills in parallel batch calls (`skillsdb get-skills <skill1> <skill2> ...` or `skillsdb get-cluster <domain>`), execute multi-query searches concurrently (`skillsdb search-multi "<q1>" "<q2>"`), and swarm tasks across parallel background subagents (up to 16 workers) to minimize turnaround latency.
   - **Gemini Standard / Lean Mode**: Conserve quota and avoid prompt bloat. Fetch targeted micro-skill sections via `skillsdb get-skill <name> --section "<Section>"` or `--summary` sequentially.

To inspect or search detailed rules and workflow skills on demand, use the `customizations-db` skill or run:
`skillsdb get-active-rules` (or `python "$env:USERPROFILE\.gemini\database\db_manager.py" get-active-rules`)
