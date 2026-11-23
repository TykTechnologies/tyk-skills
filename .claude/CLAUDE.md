@../AGENTS.md

Claude Code notes for working in this repository:

- Installed as a plugin, the skills are namespaced: `/tyk-skills:<skill-name>`, for example
  `/tyk-skills:tyk-facts`. Linked with `./scripts/install-skill.sh --engine claude` they are
  plain `/tyk-facts` and so on. Their descriptions also trigger them without a slash command.
- Shared skill files never use the Codex-only `$skill-name` sigil. They name sibling skills in
  plain text and locate them with `skills/tyk-demo-builder/scripts/resolve_skill_dir.py`.
  `./scripts/validate-repo.sh` enforces this.
- This file lives in `.claude/` rather than at the repository root because the root is also the
  plugin root, where a `CLAUDE.md` is not loaded for people who install the plugin.
  `CONTRIBUTING.md` describes how to add or change a skill.
