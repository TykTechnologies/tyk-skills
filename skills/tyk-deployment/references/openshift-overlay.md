# OpenShift overlay

Used by planning (an OpenShift target) and by a readiness review (a request for the overlay).
Paths are relative to the tyk-install checkout; write generated overlays where `SKILL.md`,
"Where generated files go", says, never into a tracked path.

## Which overlay

`kubernetes/helm-self-managed/` and `kubernetes/helm-hybrid/` ship a committed
`values-openshift.yaml` (tyk-charts 5.4.0 or later). Prefer it, and pass it again on every later
`helm upgrade`. For the Helm paths without one (`helm-oss`, `helm-mdcb`), generate the overlay
from the `# Required for deploying on RedHat OpenShift` blocks:

```bash
python3 tools/profiles.py apply kubernetes/helm-oss/values.yaml \
  --profile openshift -o <out dir>/openshift-values.yaml
```

Install with the base file first, then the overlay (`-f <dir>/values.yaml -f openshift-values.yaml`).

## Why only the OpenShift profile is applied

`apply` accepts the OpenShift profile and refuses every other one, for two reasons:

1. A commented-out setting does not reliably carry the recommended value. Activating
   `# TYK_GW_ENABLESEPERATEANALYTICSSTORE=false # PROD/PERFORMANCE: Set to true` would emit
   `false`, the opposite of its own advice, and nothing in the file tells an illustrative value
   from a recommended one.
2. On Helm, an overlay replaces a list instead of merging it. A generated `extraEnvs` deletes
   every entry the base file set, and `helm template` exits 0 either way.

The OpenShift blocks are genuine "uncomment to use" settings that graft new mappings onto the base
file, so they are safe to apply. For production, performance and security, report the `audit`
findings (`production-readiness.md`) and hand back edits to a copy of the values file that keep
its existing lists. Never present a generated production overlay as hardened.

## Validate

Validate every generated overlay, and confirm it removed nothing: `helm template` exits 0 on an
overlay that silently replaced a list.

```bash
V=kubernetes/helm-oss/values.yaml
helm template check tyk-helm/tyk-oss -f $V                                  | grep -c '\- name: TYK'
helm template check tyk-helm/tyk-oss -f $V -f <out dir>/openshift-values.yaml | grep -c '\- name: TYK'
```

The two counts must match. Audit findings whose recommendation is prose rather than a value need
a human decision: carry them into the plan or the review.
