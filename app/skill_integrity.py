"""Hash course skills and compare them to the committed lockfile.

Report export stamps the result into docs/report.md and the PDF cover.
A mismatch fails the export. Markers can re-run ``python manage.py skills-verify``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / ".agents" / "skills.lock.json"
LOCK_ENV = "FASTSTARTER_SKILLS_LOCK"
_LOCK_ENV_ALIASES = (LOCK_ENV, "FASTMVC_SKILLS_LOCK")

_GLOBS = (
    ".agents/skills/**/*.md",
    ".cursor/skills/**/*.md",
)
_EXTRA_FILES = ("AGENTS.md",)

_COMMENT_RE = re.compile(
    r"<!-- student-build:skill-integrity\n.*?\n-->\n?",
    re.DOTALL,
)
_SECTION_RE = re.compile(
    r"^## Skill integrity\n.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class IntegrityResult:
    ok: bool
    root: str
    expected_root: str | None
    files: dict[str, str]
    mismatches: tuple[str, ...]
    lock_path: Path = LOCK_PATH

    @property
    def status(self) -> str:
        return "pass" if self.ok else "fail"


def file_digest(path: Path) -> str:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def protected_relpaths(root: Path = REPO_ROOT) -> list[str]:
    found: set[str] = set()
    for pattern in _GLOBS:
        for path in root.glob(pattern):
            if path.is_file():
                found.add(path.relative_to(root).as_posix())
    for name in _EXTRA_FILES:
        path = root / name
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
    return sorted(found)


def current_files(root: Path = REPO_ROOT) -> dict[str, str]:
    return {rel: file_digest(root / rel) for rel in protected_relpaths(root)}


def root_digest(files: dict[str, str]) -> str:
    blob = "".join(f"{path}  {digest}\n" for path, digest in sorted(files.items()))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_lock(files: dict[str, str] | None = None) -> dict[str, str | dict[str, str]]:
    hashed = files if files is not None else current_files()
    return {
        "algorithm": "sha256",
        "normalization": "utf-8-lf",
        "root": root_digest(hashed),
        "files": dict(sorted(hashed.items())),
    }


def write_lock(root: Path = REPO_ROOT) -> Path:
    if not any(os.environ.get(name) == "1" for name in _LOCK_ENV_ALIASES):
        raise SystemExit(
            "skills-lock is for course authors updating the official skills. "
            f"Set {LOCK_ENV}=1 in the environment, then re-run."
        )
    payload = build_lock()
    dest = root / LOCK_PATH.relative_to(REPO_ROOT) if root != REPO_ROOT else LOCK_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


def load_lock(root: Path = REPO_ROOT) -> dict | None:
    path = root / LOCK_PATH.relative_to(REPO_ROOT)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def verify(root: Path = REPO_ROOT) -> IntegrityResult:
    files = current_files(root)
    root_hash = root_digest(files)
    lock = load_lock(root)
    if lock is None:
        return IntegrityResult(
            ok=False,
            root=root_hash,
            expected_root=None,
            files=files,
            mismatches=(f"missing lock: {LOCK_PATH.relative_to(REPO_ROOT).as_posix()}",),
        )

    expected_files = {str(k): str(v) for k, v in (lock.get("files") or {}).items()}
    expected_root = lock.get("root")
    mismatches: list[str] = []

    for rel, digest in files.items():
        if rel not in expected_files:
            mismatches.append(f"{rel}: extra (not in lock)")
        elif digest != expected_files[rel]:
            mismatches.append(f"{rel}: changed")
    for rel in expected_files:
        if rel not in files:
            mismatches.append(f"{rel}: missing")

    if expected_root and expected_root != root_hash and not mismatches:
        mismatches.append("root: changed")

    return IntegrityResult(
        ok=not mismatches,
        root=root_hash,
        expected_root=str(expected_root) if expected_root else None,
        files=files,
        mismatches=tuple(mismatches),
    )


def format_report(result: IntegrityResult) -> str:
    lines = [
        f"Skill integrity: {result.status}",
        f"Root: {result.root}",
    ]
    if result.expected_root and result.expected_root != result.root:
        lines.append(f"Expected root: {result.expected_root}")
    if result.mismatches:
        lines.append("Mismatches:")
        lines.extend(f"  - {item}" for item in result.mismatches)
    else:
        lines.append("All protected skill files match .agents/skills.lock.json.")
    return "\n".join(lines)


def stamp_markdown(markdown: str, result: IntegrityResult) -> str:
    comment = (
        "<!-- student-build:skill-integrity\n"
        f"status: {result.status}\n"
        f"root: {result.root}\n"
        f"expected_root: {result.expected_root or 'none'}\n"
        f"mismatches: {'none' if not result.mismatches else ', '.join(result.mismatches)}\n"
        "-->\n"
    )
    mismatch_lines = (
        "\n".join(f"- {item}" for item in result.mismatches)
        if result.mismatches
        else "- none"
    )
    section = (
        "## Skill integrity\n\n"
        "Course skills are hashed at export and compared to `.agents/skills.lock.json`. "
        "Do not edit `.agents/skills/`, `.cursor/skills/`, or `AGENTS.md`.\n\n"
        f"- Status: **{result.status}**\n"
        f"- Root: `{result.root}`\n"
        f"{mismatch_lines}\n"
    )
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if _COMMENT_RE.search(text):
        text = _COMMENT_RE.sub(comment, text, count=1)
    else:
        text = comment + "\n" + text.lstrip("\n")
    if _SECTION_RE.search(text):
        text = _SECTION_RE.sub(section.rstrip() + "\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n" + section
    if not text.endswith("\n"):
        text += "\n"
    return text


def require_clean(root: Path = REPO_ROOT) -> IntegrityResult:
    result = verify(root)
    if not result.ok:
        raise SystemExit(
            format_report(result)
            + "\n\nRestore the course skills (or the lock, if you are the course author) "
            "before exporting the report. Editing skills is an integrity fail."
        )
    return result
