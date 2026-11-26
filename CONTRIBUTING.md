# Contributing

Thank you for helping to keep these skills accurate. Read [`AGENTS.md`](AGENTS.md) first: it is the
contract every skill follows and the one changes are reviewed against.

## Report a problem

Open an issue with the form that fits:

- **Wrong fact**: a statement in a skill that a running stack or a public page contradicts.
- **Skill does not trigger, or triggers wrongly**: the prompt you used and what happened.
- **Script or recipe fails**: the command, the output and your versions.
- **New skill request**: the job an agent should be able to do.

Security problems never go in an issue. See [`SECURITY.md`](SECURITY.md).

## Propose a change

1. Fork the repository and create a branch from `main`.
2. Make the change. Keep it to one topic per pull request.
3. Run `./scripts/validate-repo.sh --skip-compose` (below) until it passes.
4. Open a pull request and fill in the template: what changed, how you checked each new or
   changed statement, and the validator result.

A Tyk maintainer reviews every pull request. Nothing is pushed directly to `main`.

## The rule for every statement

Every statement in a skill is checked before it is written, in one of two ways:

- **On a running stack.** You ran the step on a Tyk stack and read the real response.
- **Against a public page.** A page on https://tyk.io/docs/ (or another public page from Tyk) states
  it.

Anything that cannot be checked either way is left out. Say in the pull request how you checked
each statement (the command, image tag and date, or the URL); the skill text itself carries only the
fact.

**Skill text states facts. It never narrates how a fact was checked.**

- Write the fact plainly, in the present tense, scoped to the version where the behaviour is
  version-specific: "On Gateway 5.16.0, a JWT API whose policy has a quota answers
  `403 {"error": "Quota exceeded"}` while Redis is down." Not "live-verified on 5.16.0, 2026-11-19,
  a JWT API ...".
- Keep the public documentation link that backs a statement, as a plain link ("See
  https://tyk.io/docs/..."), with no label.
- Where the product behaves differently from what the documentation says, say both plainly: "On
  Gateway 5.16.0 the method-level limit applies only when ...; the documentation example without a
  `paths` entry does not limit."
- Example requests and real response bodies stay; they are facts about the product. Do not caption
  them as coming from a run.
- A lab or "how to reproduce" section the user follows stays; passages about how the skill's
  authors checked things do not.
- No evidence labels (`live-verified`, `docs-verified`, `source-verified`), no "(live)",
  "proved live", "observed on", "measured on", "tested on" or run dates in skill text. The validator
  rejects the labels, "(live" and "proved live" anywhere under `skills/`.

You may read the public source of an open source Tyk component at the matching release tag to
understand behaviour or to check a claim. In the skill, write what you learned as a plain fact, or
back it with a public page. A link to a file in a public Tyk GitHub repository at a release tag may
stay as a plain link; it is never a step the reader has to perform. No skill may tell its reader to
clone, register, fetch or read Tyk source code, and no skill may depend on a local copy of it. The
validator rejects text that does.

Never quote, paraphrase or identify private code: no private repository names, internal paths,
identifiers, commit hashes, ticket numbers or unreleased behaviour. Licensed components
(Dashboard, Developer Portal, Operator, MDCB, Tyk Sync, AI Studio Enterprise) are described only by
their observed behaviour and their public documentation.

Also keep out of every skill: customer or organisation names, licences, credentials, private keys,
generated bundles, security vulnerabilities (report those through `SECURITY.md`), and internal
plans or roadmap.

## Add a skill

### Folder shape

```text
skills/<skill-name>/
├── SKILL.md              frontmatter and the instructions an agent follows
├── agents/openai.yaml    Codex display name, short description and default prompt
├── references/           detail the agent reads when SKILL.md points to it (optional)
├── scripts/              standard-library Python or POSIX shell, with test_*.py beside them (optional)
└── assets/               templates, lab stacks, examples (optional)
```

### Frontmatter

```yaml
---
name: tyk-example-skill
description: <what the skill does, in one sentence>. Use when <the requests and symptoms that should trigger it>. Not for <the nearest job it does not do>, which is the `tyk-other-skill` skill.
---
```

- `name` is lowercase and hyphenated, starts with `tyk-`, and matches the folder name.
- `description` aims for about 400 characters and must stay under 1,024. It says what the skill
  does, then **Use when** with the words a user would actually type (error messages and status
  codes included), then **Not for** with the nearest neighbouring job and the skill that handles it.
- Keep the description vendor-neutral: no agent or model vendor names, so every agent can trigger it.
- Name sibling skills in plain text (for example "the `tyk-facts` skill"), never with the Codex-only
  `$` sigil. Locate a sibling's files with `skills/tyk-demo-builder/scripts/resolve_skill_dir.py`.

### References

- One topic per file, linked from `SKILL.md` with a line saying when to read it.
- Every reference that records facts starts with one `Last verified:` line giving the date and the
  exact versions or image tags it was checked against, for example
  `Last verified: 2026-11-17 against Tyk Gateway 5.16.0 (tykio/tyk-gateway:v5.16.0)`. That line is
  maintenance metadata; nothing else in the reference says how or when a fact was checked.
- State each fact plainly with its public link, as described above.
- Resolve versions live (Docker Hub tags, the release notes) rather than writing "latest".

### Scripts and tests

- Python uses the standard library. A third-party import must be guarded by `try`/`except
  ImportError` with a fallback, or declared in a `requirements*.txt` file at the root.
- Ship tests as `scripts/test_*.py`; the validator runs every skill's tests.
- Anything a script generates goes to the user's workspace
  (`${TYK_WORKSPACE:-~/tyk-workspace}/<project>/`), never into this repository.
- Published ports bind to `127.0.0.1` unless the user asks otherwise.

### Register it

- Add the name to `SKILL_NAMES` in `scripts/validate_repository.py` and to `SKILLS` in
  `scripts/install-skill.sh`.
- Add a row to the skills table in `README.md` and an entry under the next version in
  `CHANGELOG.md`.
- Add four positive and two near-miss prompts to `evals/routing/prompts.jsonl`.

## Validate

```bash
./scripts/validate-repo.sh --skip-compose
```

It checks the repository contracts (required files, skill registration, residue and secret
patterns, that no skill sends its reader to Tyk source, and that no skill carries evidence labels
or run captions), compiles and unit-tests the Python,
lints the shell, generates and validates clean-room demo bundles, validates the skill packages,
builds the example sizing pack and diagram, and validates the plugin manifests for Agent Plugins,
Claude Code and Codex. Without `--skip-compose` it also validates the generated Compose files with
Docker. CI runs the same script on every pull request and on every release tag.

Optional dependencies: `python3 -m pip install -r requirements-diagrams.txt` (Pillow, for the
diagram rasteriser check) and `shellcheck`.

## Versions and manifests

`plugin.json` at the repository root is the one place the plugin's version and description are
edited. After changing either, run:

```bash
python3 scripts/sync_manifests.py
```

It copies them, with the support, privacy and terms links, into `.claude-plugin/` and
`.codex-plugin/`. The validator fails when any manifest disagrees with `plugin.json`, or when
`CHANGELOG.md` has no entry for the version. If you have Claude Code installed, also run
`claude plugin validate --strict .`.

## Licence

By contributing you agree that your contribution is licensed under the
[Mozilla Public License 2.0](LICENSE), the licence of this repository.
