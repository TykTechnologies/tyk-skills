#!/usr/bin/env python3
"""Scan a demo bundle for host-specific data and credentials before sharing.

The scan reads every file that is not git-ignored and is not a symbolic link.
`--strict-runtime` additionally refuses known runtime secrets, any git-ignored path still on
disk and any symbolic link, because an archive or copy of the directory carries ignored files
and follows links that the scan never read. A clean result means these
checks found nothing; it is one defence, not proof that the bundle holds no secret.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fnmatch
import json
from pathlib import Path
import re
import sys
from typing import Iterable


EXCLUDED_PARTS = {".git", "__pycache__"}
PRIVATE_KEY = re.compile("-----BEGIN " + r"(?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
TOKEN_PATTERNS = (
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
# An assignment line: optionally a YAML list item (`- NAME=value`, the Compose list form of
# `environment:`) or a shell declaration (`export`, `local`, `readonly`, `declare -r`, `typeset`).
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)^[ \t]*(?:-[ \t]+)?(?:(?:export|local|readonly|typeset|declare(?:[ \t]+-[A-Za-z]+)*)[ \t]+)?"
    r"([A-Z_][A-Z0-9_]*)[ \t]*[:=][ \t]*(.*?)[ \t]*$"
)
# Any quoted name with a quoted value; the name is judged by credential_name(), so
# `"node_secret"`, `"client_secret"`, `"auth_token"` and `"apiKey"` are covered like env names.
JSON_CREDENTIAL = re.compile(r'''["']([A-Za-z_][A-Za-z0-9_-]*)["']\s*:\s*["']([^"'\n]+)["']''')
# `${NAME:-default}` and friends: the default is a literal and is judged like any other value.
DEFAULTED_REFERENCE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=+](.*)\}$", re.DOTALL)
# The value of a credential-named field is safe only when it is clearly not a secret:
# - anywhere: empty, null/none/true/false, a variable reference or explicit placeholder
#   (`${VAR}`, `$VAR`, `$(...)`, `${{ ... }}`, `<...>`, `__NAME__`, an AI Studio secret
#   reference `$SECRET/NAME`, and the words in PLACEHOLDER_WORDS), or the name of a key or
#   certificate file;
# - in source code only (SOURCE_CODE_SUFFIXES), and only when unquoted: a variable or
#   attribute name, a function call, or a path built from variables.
# A literal in a data or configuration file (env, JSON, YAML, TOML, INI, text, Markdown,
# shell) is a finding however ordinary it looks: `PASSWORD=CorrectHorseBatteryStaple123`
# and `"api_key": "abcdef0123..."` are exactly what this scan exists to stop.
# "stub-key" is what the AI Studio companion's stub LLM backends accept; it opens nothing.
PLACEHOLDER_WORDS = ("changeme", "change-me", "replace-me", "redacted", "[redacted]", "placeholder", "unused", "stub-key")
PLACEHOLDER_VALUE = re.compile(
    r"^(?:null|none|true|false|\$\{\{.*\}\}|\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|__[A-Z0-9_]+__|<[^>]+>"
    r"|\$\([^)]*\)|\$SECRET/[A-Za-z0-9_.-]+|\*{3,}|x{3,}|" + "|".join(re.escape(word) for word in PLACEHOLDER_WORDS) + r"|[^/\s]+\.(?:key|pem|crt))$",
    re.IGNORECASE,
)
CODE_REFERENCE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_.]*\(.*\)|[A-Za-z_][A-Za-z0-9_.]*(?:\[[^\]]*\])*|(?:[A-Za-z_][A-Za-z0-9_]*\s*/\s*)+[\"'][^\"']+\.(?:key|pem|crt)[\"'])$"
)
# Languages where an unquoted identifier is a reference rather than a string. Shell is not one:
# `TOKEN=abc` assigns the literal "abc".
SOURCE_CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".go", ".java", ".rb", ".rs", ".kt", ".cs", ".lua"}
# A JSON `key` field is often a harmless name ("key": "Authorization"), so it is reported only
# when its value looks like minted token material: long, no spaces, letters and digits.
JSON_KEY_FIELD = re.compile(r'''["']key["']\s*:\s*["']([^"'\n]+)["']''')
TOKEN_LIKE = re.compile(r"^(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9_.+/=-]{20,}$")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}"


def ignore_patterns(root: Path) -> tuple[str, ...]:
    patterns = [".env", ".runtime/"]
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        try:
            lines = gitignore.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        patterns.extend(
            line.strip() for line in lines
            if line.strip() and not line.lstrip().startswith(("#", "!"))
        )
    return tuple(dict.fromkeys(patterns))


def path_is_ignored(relative: Path, patterns: Iterable[str]) -> bool:
    value = relative.as_posix()
    prefixes = ["/".join(relative.parts[:index]) for index in range(1, len(relative.parts) + 1)]
    for raw_pattern in patterns:
        pattern = raw_pattern[2:] if raw_pattern.startswith("./") else raw_pattern
        if not pattern:
            continue
        directory_pattern = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        # gitignore semantics: a leading `**/` matches in every directory, the root included,
        # and a pattern without a slash matches a name at any depth (`dir/` only a directory).
        for candidate in ((pattern, pattern[3:]) if pattern.startswith("**/") else (pattern,)):
            if "/" not in candidate:
                names = relative.parts[:-1] if directory_pattern else relative.parts
                if any(fnmatch.fnmatchcase(name, candidate) for name in names):
                    return True
            if fnmatch.fnmatchcase(value, candidate):
                return True
            if directory_pattern and any(fnmatch.fnmatchcase(prefix, candidate) for prefix in prefixes):
                return True
    return False


def scan_files(root: Path) -> Iterable[Path]:
    patterns = ignore_patterns(root)
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path_is_ignored(relative, patterns):
            continue
        if path.is_file() and not path.is_symlink():
            yield path


def ignored_present(root: Path) -> list[Path]:
    """Ignored paths that exist on disk: the scan skips them, but a zip of the directory does not.

    Reports the topmost ignored path only (`.runtime`, not every file under it). `.git` is not
    reported: sharing through git leaves ignored files behind, and history is out of scope.
    """
    root = root.expanduser().resolve()
    patterns = ignore_patterns(root)
    found: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if relative.parts[0] == ".git":
                continue
            if path_is_ignored(relative, patterns):
                found.append(relative)
            elif path.is_dir() and not path.is_symlink():
                pending.append(path)
    return sorted(found, key=str)


def symlinks_present(root: Path) -> list[Path]:
    """Symbolic links in the bundle. The scan never reads through a link, but `zip -r` and
    `cp -R` follow it, so an archive can carry a file from outside the bundle unread."""
    root = root.expanduser().resolve()
    found: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if relative.parts[0] == ".git":
                continue
            if path.is_symlink():
                found.append(relative)
            elif path.is_dir():
                pending.append(path)
    return sorted(found, key=str)


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def opens_unclosed_substitution(value: str) -> bool:
    """True when the value starts a command substitution that this line does not close.

    A multi-line assignment such as

        secret="$(curl -fsS "https://example.test/api" \\
          | jq -r '.value')"

    is matched one line at a time, so the first line is an incomplete expression
    rather than a literal. Reporting it as an embedded credential is a false
    positive, and a gate that cries wolf is one people stop reading — which
    matters here, because this is the last check before a bundle is shared.
    """
    opened = value.count("$(")
    if not opened:
        return False
    return value.count(")") < opened or value.rstrip().endswith("\\")


def credential_value_is_safe(value: str, source_code: bool = False) -> bool:
    value = value.strip().rstrip(",;").strip()
    quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "'\""
    if quoted:
        value = value[1:-1].strip()
    if opens_unclosed_substitution(value):
        return True
    defaulted = DEFAULTED_REFERENCE.fullmatch(value)
    if defaulted:
        return credential_value_is_safe(defaulted.group(1), source_code)
    if not value or PLACEHOLDER_VALUE.fullmatch(value):
        return True
    return source_code and not quoted and bool(CODE_REFERENCE.fullmatch(value))


def credential_name(name: str) -> bool:
    # camelCase names are judged like their SNAKE_CASE form: apiKey -> API_KEY. Kebab-case is
    # left alone: bundles use names such as "api-key" for modules, not for credentials.
    upper = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()
    exact = {
        "TOKEN", "LICENSE", "LICENSE_KEY", "LICENSEKEY", "PASSWORD", "PASSWD", "PASS", "SECRET", "API_KEY",
        "PRIVATE_KEY", "SECRET_KEY", "SIGNING_KEY", "ACCESS_KEY",
    }
    suffixes = tuple(f"_{item}" for item in exact)
    # Names that end in SECRET with no separator, such as TYK_DB_ADMINSECRET.
    return upper in exact or upper.endswith(suffixes) or upper.endswith("SECRET")


def scan_text(relative: Path, text: str, forbid: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    lowered = text.casefold()
    for term in forbid:
        start = 0
        needle = term.casefold()
        while needle and (offset := lowered.find(needle, start)) >= 0:
            findings.append(Finding(relative, line_number(text, offset), f"forbidden term {term!r}"))
            start = offset + len(needle)
    for needle, kind in (("/" + "Users" + "/", "absolute macOS user path"), ("file:" + "///", "local file URI")):
        start = 0
        while (offset := text.find(needle, start)) >= 0:
            findings.append(Finding(relative, line_number(text, offset), kind))
            start = offset + len(needle)
    for match in PRIVATE_KEY.finditer(text):
        findings.append(Finding(relative, line_number(text, match.start()), "private key material"))
    for pattern in TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(Finding(relative, line_number(text, match.start()), "token material"))
    source_code = relative.suffix.lower() in SOURCE_CODE_SUFFIXES
    for match in CREDENTIAL_ASSIGNMENT.finditer(text):
        if credential_name(match.group(1)) and not credential_value_is_safe(match.group(2), source_code):
            findings.append(Finding(relative, line_number(text, match.start()), "embedded credential or license"))
    for match in JSON_CREDENTIAL.finditer(text):
        # A quoted JSON value is a string literal in every file type.
        if credential_name(match.group(1)) and not credential_value_is_safe(f'"{match.group(2)}"'):
            findings.append(Finding(relative, line_number(text, match.start()), "embedded credential or license"))
    for match in JSON_KEY_FIELD.finditer(text):
        if TOKEN_LIKE.fullmatch(match.group(1).strip()):
            findings.append(Finding(relative, line_number(text, match.start()), "embedded key value"))
    return findings


BRAND_DIR = Path("portal") / "brand"
BRAND_ASSET_KEYS = ("logo", "logo_on_dark", "favicon", "hero")


def brand_kit_findings(root: Path, allow_own_brand: bool = False) -> list[Finding]:
    """A neutral bundle carries only the generator's placeholder brand kit.

    An organisation's brand kit (logo, colours, copy) belongs in that organisation's own
    bundle and nowhere else, so a kit without `"placeholder": true`, or a file in a
    placeholder kit that its brand.json does not name, is reported unless the caller says
    this is the brand owner's own bundle (--own-brand).
    """
    brand_dir = root / BRAND_DIR
    manifest = brand_dir / "brand.json"
    if allow_own_brand or not brand_dir.is_dir():
        return []
    relative = BRAND_DIR / "brand.json"
    try:
        brand = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        brand = None
    if not isinstance(brand, dict) or brand.get("placeholder") is not True:
        return [Finding(relative, 1, "organisation brand kit in portal/brand/ (not the neutral placeholder); pass --own-brand only in that organisation's own bundle")]
    expected = {"brand.json", "content.json"} | {brand[key] for key in BRAND_ASSET_KEYS if isinstance(brand.get(key), str)}
    return [
        Finding(path.relative_to(root), 1, "file in the placeholder brand kit that brand.json does not name")
        for path in sorted(brand_dir.rglob("*"))
        if path.is_file() and path.relative_to(brand_dir).as_posix() not in expected
    ]


def scan_bundle(root: Path, forbid: Iterable[str] = (), allow_own_brand: bool = False) -> list[Finding]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"bundle directory does not exist: {root}")
    findings: list[Finding] = []
    terms = tuple(dict.fromkeys(term for term in forbid if term))
    for path in scan_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        findings.extend(scan_text(path.relative_to(root), text, terms))
    findings.extend(brand_kit_findings(root, allow_own_brand))
    return findings


def runtime_artifacts(root: Path) -> list[Path]:
    """Return secret-bearing or compiled runtime paths that must not be shared."""
    root = root.expanduser().resolve()
    # migration/imported-keys.json holds the key-import module's minted consumer keys, which
    # are live credentials once import-keys.sh has run.
    candidates = [root / ".env", root / ".runtime", root / "generated" / "certs", root / "migration" / "imported-keys.json"]
    # Companion stacks (for example companion/ai-studio/) keep their own licence and minted
    # secrets in secret.env, and their runtime state and databases under .runtime/ and data/.
    for companion in sorted((root / "companion").glob("*")):
        if companion.is_dir():
            candidates.extend((companion / "secret.env", companion / "admin.env", companion / ".runtime", companion / "data"))
    found = [path.relative_to(root) for path in candidates if path.exists()]
    for pattern in ("*.so", "*.zip", "*.key", "*.pem"):
        found.extend(path.relative_to(root) for path in root.rglob(pattern) if path.is_file())
    return sorted(set(found), key=str)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--forbid", action="append", default=[], metavar="TERM", help="case-insensitive term to reject; repeat as needed")
    parser.add_argument(
        "--strict-runtime", action="store_true",
        help="fail when local runtime secrets, certificates, compiled artifacts or any other git-ignored file exist; "
             "run it on the directory you are about to share",
    )
    parser.add_argument(
        "--own-brand", action="store_true",
        help="this is your organisation's own bundle, so its Portal brand kit (portal/brand/) may be your own; "
             "without it only the neutral placeholder kit passes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        findings = scan_bundle(args.bundle, args.forbid, args.own_brand)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(f"ERROR: {finding}", file=sys.stderr)
        return 1
    if args.strict_runtime:
        # The scan above skips git-ignored paths, but a zip or copy of the directory does not, so a
        # strict run (the one before sharing) refuses anything ignored that is still on disk.
        artifacts = runtime_artifacts(args.bundle)
        ignored = [path for path in ignored_present(args.bundle) if path not in artifacts]
        for artifact in artifacts:
            print(f"ERROR: {artifact}: local runtime artifact must be removed before sharing", file=sys.stderr)
        for path in ignored:
            print(f"ERROR: {path}: git-ignored, so not scanned, but an archive of this directory would include it; remove it before sharing", file=sys.stderr)
        links = [path for path in symlinks_present(args.bundle) if path not in artifacts and path not in ignored]
        for path in links:
            print(f"ERROR: {path}: symbolic link, so not scanned, but an archive of this directory would follow it; "
                  "replace it with the file or remove it before sharing", file=sys.stderr)
        if artifacts or ignored or links:
            return 1
    else:
        ignored = ignored_present(args.bundle)
        if ignored:
            print(f"NOTE: {len(ignored)} git-ignored path(s) were not scanned (for example {ignored[0]}); "
                  "run with --strict-runtime before sharing")
    print(f"Sanitized bundle: {args.bundle.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
