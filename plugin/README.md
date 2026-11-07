# AntiGraviton SkillsDB (`customizations-db`)

- **Developer**: `scorpion421` (`MickyFoley`)
- **License**: [GNU AGPL v3.0 + Commons Clause v1.0](../LICENSE) (`AGPL-3.0-only WITH Commons-Clause-1.0` - Non-Commercial / No Marketplace Resale)
- **Suite Version**: AntiGraviton `v2.1.3` (`SkillsDB v4.1.3`)
- **GitHub Repository**: [https://github.com/scorpion421/AntiGraviton](https://github.com/scorpion421/AntiGraviton)
- **Local Gitea Service**: [http://localhost:3000](http://localhost:3000) (`skillsdb gitea-status`)

## Core capabilities

1. **On-demand FTS5 micro-skills (`~97.6%` per-turn token reduction)**: Consolidates global rules and workflow skills into `~/.gemini/database/customizations.db` with surgical section slicing (`skillsdb get-skill <name> --section "<Section>"`).
2. **Autonomous episodic project memory (`.agents/memory.db`)**: Tracks architectural decisions, active tasks, facts, and session snapshots per project workspace.
3. **Temporal epoch slicing & cross-project isolation**: Automatically segments multi-day conversations into date/gap epochs and prevents cross-project context bleeding.
4. **Mid-session UTF-8 self-healing & PreToolUse guardrails**: Automatically restores Windows UTF-8 process manifests after Antigravity IDE updates and enforces typography, secret-leak, and Keep a Changelog 1.1.0 invariants.
5. **Local Gitea & GitHub release synchronization**: Zero-token-leak Git credential helper and automated SemVer 2.0.0 / Keep a Changelog 1.1.0 release publishing (`skillsdb gitea-sync --releases --github`).
6. **Multi-pool Golden-Ratio quota router & AST contract mapper (`v4.1.3`)**: Governs `Ultra (16 threads)` and `Pro (8 threads)` subscriptions across all 4 quota pools (`Argon`, `Flash`, `Claude 5.5`, `GPT-OSS 120B`, `CLI`, `AI Studio`, `Web`), guarantees 100% Argon/Claude coding quality, and maps multi-file AST contracts (`skillsdb ast-map`) at up to `98.6%` token compression.
