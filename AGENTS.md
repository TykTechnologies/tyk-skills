# Tyk Skills Agent Contract

## Purpose

These skills help an AI coding agent evaluate and use Tyk accurately: state facts that are true for the release in question; write API definitions, keys and policies, GitOps delivery, MCP and AI gateway configuration and Developer Portal branding that work on the first run; build a demo or proof of concept that proves each capability; draw the deployment, size it, and plan a migration from another gateway. They are public and vendor-written; they favour verification over recollection.

Both the Codex CLI and the Claude Code CLI are supported. Invoke a skill with `$name` on Codex or `/name` (or `/tyk-skills:name` when installed as a plugin) on Claude Code. Shared skill files reference sibling skills by plain name only and locate them with `skills/tyk-demo-builder/scripts/resolve_skill_dir.py`.

## Facts Before Fluency

- Never state a Tyk version, image tag, licence boundary, price or feature availability from memory. Resolve versions live (Docker Hub tags, GitHub releases, the release notes at https://tyk.io/docs/developer-support/release-notes/overview) and quote licensing and packaging from the `tyk-facts` skill, which carries the vendor's current wording with the date it was last reviewed.
- Verify every behaviour claim in one of two ways, strongest first: run the step on the user's own Tyk stack and read the real response; or cite the public documentation page (https://tyk.io/docs/) that states it. Nothing in these skills needs Tyk source code: never ask the user to clone, register, fetch or read a Tyk repository, and never depend on a local copy of one.
- When you answer the user, say which points you checked on their running stack (the command or page and the image tag) and which come from a public page (its URL), and say plainly when a point was not checked. Never present a point taken from a page as something seen on the stack.
- Skill text states facts. It never narrates how a fact was checked: no evidence labels, no run dates or captions such as "(live)", "proved live" or "observed on". Write the fact in the present tense, scoped to the version where the behaviour is version-specific, and link the public page that backs it as a plain link. A link to a file in a public Tyk GitHub repository at a release tag may stay as a plain link; it is never a step to perform. `./scripts/validate-repo.sh` rejects the labels and captions.
- The documentation site describes the current release, so a pinned or older build may differ from it in either direction. When the stack and the page disagree, the stack wins for that version; state both plainly ("On Gateway 5.16.0 X happens; the documentation says Y") and link the page.
- Documentation can describe behaviour that has not been released yet. Never present it as available in a released version; cross-check the release notes for the target version first.
- Prefer Tyk OAS API definitions over the classic format for everything new, and say so when an example or a tool defaults to classic.
- Nothing written by or for these skills may quote, paraphrase or identify private Tyk code: no private repository names, internal paths, identifiers, commit hashes or unreleased behaviour. Licensed components (Dashboard, Developer Portal, Operator, MDCB, Tyk Sync, AI Studio Enterprise) are described only by their observed behaviour and their public documentation.

## Where Deliverables Go

- Keep everything generated (demo bundles, briefs, sizing packs, diagrams, runbooks) outside this plugin's folder, in the user's project workspace: `${TYK_WORKSPACE:-~/tyk-workspace}/<project>/` (`demos/<slug>/`, `sizing/`, `docs/`), unless the user names another location. One folder per project.
- Never write requirements, payloads, screenshots, branding, endpoints, credentials, licences, private keys, certificates, runtime state or generated bundles into this repository.

## Demo And Proof Of Concept Runs

- Treat the supplied requirements as the source of truth. Select only the modules needed to prove the requested behaviour.
- Never mark a requirement `live` without executable or browser-visible evidence from the deployed stack. Architecture-only capabilities stay talk track. Report blockers honestly.
- Run static validation, idempotent configuration, restart verification, full rehearsal and sanitisation when prerequisites allow.
- The builder needs a Tyk Dashboard licence (a free trial key is available; the `tyk-facts` skill has the current path). Every production or long-lived installation, including the open source Gateway alone, goes through the `tyk-deployment` skill, which works on a checkout of the public tyk-install repository (https://github.com/TykTechnologies/tyk-install).

## Changing The Skills

- A change to the reusable builder, engine or references must stay neutral and self-contained, carry tests where the code has them, and pass `./scripts/validate-repo.sh`. Do not claim completion if it fails; fix the failure or report the exact blocker.
- Reference files that record facts carry one `Last verified: <date> against <versions>` line at the top as maintenance metadata; nothing else in a skill says how or when a fact was checked. Treat a conflict between a reference file and the live stack or the public documentation as a bug in the reference, and fix the reference.
- Maintainers check every statement before it goes in, on a running stack or against a public page, and leave out anything that cannot be checked. The skill text carries only the fact and its public link. No skill text may tell the reader to clone or read Tyk source; `./scripts/validate-repo.sh` enforces this. `CONTRIBUTING.md` has the full process for adding or changing a skill.
- Commit, push and pull-request actions require an explicit instruction from the user. Never push directly to the default branch.
