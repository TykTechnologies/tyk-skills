## What changed

<!-- One topic per pull request. Name the skills and files you touched. -->

## How each new or changed statement was checked

<!--
Every statement in a skill is checked on a running stack or against a public page before it goes
in (see CONTRIBUTING.md). For each one, give here:
- checked on a stack: the command or page, the image tag and the date
- checked against a page: the https://tyk.io/docs/ URL
The skill text itself states only the fact and its public link, with no evidence label or run
caption.
-->

## Checklist

- [ ] `./scripts/validate-repo.sh --skip-compose` passes (paste the last line below).
- [ ] No skill tells its reader to clone, fetch or read Tyk source code.
- [ ] Skill text states facts with their public links and never narrates how they were checked.
- [ ] Nothing quotes or identifies private code, a customer, a licence, a credential or a key.
- [ ] References I changed carry an updated `Last verified:` line.
- [ ] A new skill is registered in `scripts/validate_repository.py`, `scripts/install-skill.sh`, the README skills table and `CHANGELOG.md`.
- [ ] If I changed the version or description in `plugin.json`, I ran `python3 scripts/sync_manifests.py`.

```text
<validator output>
```
