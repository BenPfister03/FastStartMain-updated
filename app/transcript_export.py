"""Export native Guide agent transcripts for the report build."""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "transcripts"
TRANSCRIPTS_HEADING = "## Session transcripts"
_TRANSCRIPTS_SECTION_RE = re.compile(
    r"^## Session transcripts\n.*?(?=^## (?:Competency \(student-judge\)|Skill integrity)|\Z)",
    re.MULTILINE | re.DOTALL,
)
_ENV_DIR = "FASTSTARTER_TRANSCRIPTS_DIR"
_ENV_DIR_ALIASES = (_ENV_DIR, "FASTMVC_TRANSCRIPTS_DIR")


@dataclass(frozen=True)
class TranscriptExportResult:
    """Paths written for one report export."""

    out_dir: Path
    index_path: Path | None
    markdown_files: tuple[Path, ...]
    zip_path: Path | None
    source_roots: tuple[Path, ...]
    found: int


def cursor_project_slug(repo: Path = REPO_ROOT) -> str:
    """Match Cursor's `~/.cursor/projects/<slug>/` naming."""
    resolved = str(repo.resolve())
    return (
        resolved.replace(":", "")
        .replace("\\", "-")
        .replace("/", "-")
        .replace(" ", "-")
    )


def discover_transcript_roots(repo: Path = REPO_ROOT) -> list[Path]:
    """Locate native agent transcript folders for this workspace."""
    roots: list[Path] = []
    env = ""
    for name in _ENV_DIR_ALIASES:
        env = os.environ.get(name, "").strip()
        if env:
            break
    if env:
        roots.append(Path(env).expanduser())

    slug = cursor_project_slug(repo)
    home_projects = Path.home() / ".cursor" / "projects"
    for variant in {slug, slug[:1].lower() + slug[1:] if slug else slug}:
        if variant:
            roots.append(home_projects / variant / "agent-transcripts")

    # Fallback: any Cursor project folder whose slug contains the repo name.
    name = repo.resolve().name.lower()
    if home_projects.is_dir():
        for child in home_projects.iterdir():
            if not child.is_dir():
                continue
            if name in child.name.lower():
                candidate = child / "agent-transcripts"
                if candidate not in roots:
                    roots.append(candidate)

    # Local optional drop folder (students/tools may copy jsonl here).
    roots.append(repo / ".agents" / "transcripts")
    roots.append(repo / "docs" / "_native_transcripts")

    seen: set[Path] = set()
    unique: list[Path] = []
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def find_jsonl_files(roots: list[Path] | None = None) -> list[Path]:
    """Return unique transcript source files (*.jsonl, else *.md dumps)."""
    files: list[Path] = []
    seen: set[Path] = set()
    search_roots = list(roots or discover_transcript_roots())
    zip_path = REPO_ROOT / "docs" / "transcripts.zip"
    if zip_path.is_file():
        extract_dir = REPO_ROOT / "docs" / "transcripts" / "_from_zip"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            search_roots.insert(0, extract_dir)
        except OSError:
            pass

    def _add(path: Path) -> None:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            return
        seen.add(key)
        files.append(path)

    for root in search_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            _add(path)

    if files:
        return files

    # Copilot / manual dumps: markdown chat exports under transcript folders.
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if path.name.lower() in {"index.md", "readme.md"}:
                continue
            _add(path)
    return files


def jsonl_to_markdown(path: Path, *, chat_id: str | None = None) -> str:
    """Render a Cursor-style agent jsonl into readable markdown."""
    title = chat_id or path.stem
    lines = [
        f"# Guide chat `{title}`",
        "",
        f"Source: `{path}`",
        "",
    ]
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return "\n".join(lines + [f"_Could not read file: {exc}_", ""])

    turn = 0
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            lines.extend(["### Unparsed line", "", "```", raw[:2000], "```", ""])
            continue
        role = str(row.get("role") or row.get("type") or "unknown")
        if role == "turn_ended":
            continue
        turn += 1
        lines.append(f"## Turn {turn} — {role}")
        lines.append("")
        body = _format_message(row)
        lines.append(body if body.strip() else "_empty_")
        lines.append("")
    if turn == 0:
        lines.append("_No turns found._")
        lines.append("")
    return "\n".join(lines)


def export_project_transcripts(
    *,
    out_dir: Path | None = None,
    repo: Path = REPO_ROOT,
    make_zip: bool = True,
) -> TranscriptExportResult:
    """Write all discovered transcripts under docs/transcripts/ and optional zip."""
    dest = out_dir or DEFAULT_OUT
    dest.mkdir(parents=True, exist_ok=True)
    # Clear previous export markdown/jsonl copies we own (keep folder).
    for old in dest.glob("*"):
        if old.name in {"README.md"}:
            continue
        if old.is_file() and old.suffix.lower() in {".md", ".jsonl"}:
            old.unlink(missing_ok=True)
        elif old.is_file() and old.name == "transcripts.zip":
            old.unlink(missing_ok=True)

    roots = discover_transcript_roots(repo)
    sources = find_jsonl_files(roots)
    written: list[Path] = []

    for src in sources:
        chat_id = src.stem
        md_path = dest / f"{chat_id}.md"
        if src.suffix.lower() == ".jsonl":
            md_path.write_text(jsonl_to_markdown(src, chat_id=chat_id), encoding="utf-8")
            raw_copy = dest / f"{chat_id}.jsonl"
            try:
                shutil.copy2(src, raw_copy)
            except OSError:
                raw_copy.write_text(
                    src.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
        else:
            # Already markdown (e.g. Copilot dump) — copy through.
            try:
                shutil.copy2(src, md_path)
            except OSError:
                md_path.write_text(
                    src.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
        written.append(md_path)

    index = dest / "INDEX.md"
    index_body = [
        "# Session transcripts",
        "",
        "Exported by `python manage.py report` from native Guide chats.",
        "Do not hand-edit these mid-assignment; re-export to refresh.",
        "",
        f"Chats found: **{len(written)}**",
        "",
    ]
    if written:
        index_body.append("| Chat | Markdown | Raw |")
        index_body.append("| --- | --- | --- |")
        for md in written:
            chat_id = md.stem
            index_body.append(
                f"| `{chat_id}` | [{md.name}]({md.name}) | [{chat_id}.jsonl]({chat_id}.jsonl) |"
            )
        index_body.append("")
    else:
        index_body.extend(
            [
                "_No native agent transcripts were found._",
                "",
                "Searched:",
                "",
                *[f"- `{root}`" for root in roots],
                "",
                f"Set `{_ENV_DIR}` to a folder of `.jsonl` files if your tool stores chats elsewhere.",
                "",
            ]
        )
    index.write_text("\n".join(index_body), encoding="utf-8")

    zip_path: Path | None = None
    if make_zip:
        zip_path = dest.parent / "transcripts.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(dest.iterdir()):
                if path.is_file():
                    zf.write(path, arcname=f"transcripts/{path.name}")
        print(f"Wrote {zip_path}")

    print(f"Exported {len(written)} transcript(s) to {dest}")
    return TranscriptExportResult(
        out_dir=dest,
        index_path=index if index.is_file() else None,
        markdown_files=tuple(written),
        zip_path=zip_path if zip_path and zip_path.is_file() else None,
        source_roots=tuple(roots),
        found=len(written),
    )


def merge_transcripts_section(
    markdown: str,
    result: TranscriptExportResult,
) -> str:
    """Insert or replace the Session transcripts section in docs/report.md."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if result.found:
        bullets = "\n".join(
            f"- [`{path.stem}`](transcripts/{path.name})" for path in result.markdown_files
        )
        detail = (
            f"Exported **{result.found}** Guide chat(s) to `docs/transcripts/` "
            f"(and `docs/transcripts.zip`).\n\n"
            f"Index: [docs/transcripts/INDEX.md](transcripts/INDEX.md)\n\n"
            f"{bullets}\n"
        )
    else:
        # Use forward slashes so Windows paths never hit re.sub escape rules (\U…).
        searched = "\n".join(f"- `{root.as_posix()}`" for root in result.source_roots)
        detail = (
            "No native Guide transcripts were found at export time.\n\n"
            "Searched:\n\n"
            f"{searched}\n\n"
            f"Set `{_ENV_DIR}` if chats live elsewhere.\n"
        )
    section = (
        f"{TRANSCRIPTS_HEADING}\n\n"
        "Filled by `python manage.py report` from native agent chats. "
        "Students do not paste chats here during the build.\n\n"
        f"{detail}"
    )
    # Always replace via a callable — path strings can contain backslashes that
    # re.sub would treat as escape sequences in a plain replacement template.
    def _insert_section(_match: re.Match[str]) -> str:
        return section.rstrip() + "\n\n"

    if _TRANSCRIPTS_SECTION_RE.search(text):
        text = _TRANSCRIPTS_SECTION_RE.sub(_insert_section, text, count=1)
    elif re.search(r"^## Competency \(student-judge\)\s*$", text, re.MULTILINE):
        text = re.sub(
            r"^## Competency \(student-judge\)\s*$",
            lambda _m: section + "\n## Competency (student-judge)",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    elif re.search(r"^## Skill integrity\s*$", text, re.MULTILINE):
        text = re.sub(
            r"^## Skill integrity\s*$",
            lambda _m: section + "\n## Skill integrity",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + section
    if not text.endswith("\n"):
        text += "\n"
    return text


def appendix_markdown(result: TranscriptExportResult) -> str:
    """Markdown appended into the PDF after the main report body."""
    parts = [
        "# Appendix — Session transcripts",
        "",
        "Full Guide chats exported at report build time.",
        "",
    ]
    if not result.markdown_files:
        parts.append("_No transcripts found._")
        parts.append("")
        return "\n".join(parts)
    for path in result.markdown_files:
        parts.append(path.read_text(encoding="utf-8"))
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts)


def _format_message(row: dict) -> str:
    message = row.get("message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = row.get("content")
    return _format_content(content)


def _format_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _strip_meta(content).strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(_strip_meta(part))
                continue
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                chunks.append(_strip_meta(str(part.get("text") or "")))
            elif ptype == "tool_use":
                name = part.get("name") or "tool"
                chunks.append(f"_tool: `{name}`_")
            elif ptype == "tool_result":
                chunks.append("_tool result_")
            else:
                chunks.append(f"_{ptype or 'part'}_")
        return "\n\n".join(c.strip() for c in chunks if c and c.strip())
    return _strip_meta(str(content)).strip()


def _strip_meta(text: str) -> str:
    """Drop wrapper tags that clutter student-facing exports."""
    text = re.sub(r"<timestamp>.*?</timestamp>\s*", "", text, flags=re.DOTALL)
    text = re.sub(r"</?user_query>", "", text)
    text = re.sub(r"</?agent_transcript[^>]*>", "", text)
    return text.strip()
