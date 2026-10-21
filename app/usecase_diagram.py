"""Render a UML use-case diagram PNG from a small JSON spec.

Mermaid has no real UML use-case notation (stick-figure actors, system
boundary, ellipses, «include» / «extend»). Phase 2 writes
docs/diagrams/use-case.json; this module compiles PlantUML and runs the
vendored JAR (vendor/plantuml/plantuml.jar).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_JAR = REPO_ROOT / "vendor" / "plantuml" / "plantuml.jar"
VENDOR_MANIFEST = REPO_ROOT / "vendor" / "manifest.json"
JAR_REL = "vendor/plantuml/plantuml.jar"

DEFAULT_SPEC = REPO_ROOT / "docs" / "diagrams" / "use-case.json"
DEFAULT_PNG = REPO_ROOT / "docs" / "diagrams" / "use-case.png"

_ALIAS_RE = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Actor:
    name: str
    side: str  # left | right


@dataclass(frozen=True)
class UseCase:
    name: str
    actors: tuple[str, ...]
    includes: tuple[str, ...]
    extends: tuple[str, ...]


@dataclass(frozen=True)
class DiagramSpec:
    system: str
    actors: tuple[Actor, ...]
    use_cases: tuple[UseCase, ...]


def render_usecase_png(
    spec_path: Path | None = None,
    output: Path | None = None,
) -> Path:
    spec = load_spec(spec_path or DEFAULT_SPEC)
    dest = output or DEFAULT_PNG
    dest.parent.mkdir(parents=True, exist_ok=True)
    plantuml = to_plantuml(spec)
    puml = dest.with_suffix(".puml")
    puml.write_text(plantuml, encoding="utf-8")
    _run_plantuml(puml, dest)
    return dest


def load_spec(path: Path) -> DiagramSpec:
    if not path.is_file():
        raise SystemExit(
            f"Missing {path.as_posix()}. Write the use-case JSON, then re-run "
            "python manage.py usecase."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path.as_posix()}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Use-case spec must be a JSON object.")

    system = str(payload.get("system") or "System").strip() or "System"
    actors = _parse_actors(payload.get("actors") or [])
    if not actors:
        raise SystemExit("Use-case spec needs a non-empty actors array.")
    actor_names = tuple(actor.name for actor in actors)

    raw_cases = payload.get("use_cases") or []
    if not isinstance(raw_cases, list) or not raw_cases:
        raise SystemExit("Use-case spec needs a non-empty use_cases array.")
    cases: list[UseCase] = []
    for item in raw_cases:
        parsed = _parse_use_case(item, actor_names)
        if parsed is not None:
            cases.append(parsed)
    if not cases:
        raise SystemExit("Use-case spec has no named use cases.")

    includes_extra = _parse_relations(payload.get("includes") or [])
    extends_extra = _parse_relations(payload.get("extends") or [])
    by_name = {case.name: case for case in cases}
    for source, target in includes_extra:
        if source in by_name:
            current = by_name[source]
            by_name[source] = UseCase(
                name=current.name,
                actors=current.actors,
                includes=tuple(dict.fromkeys((*current.includes, target))),
                extends=current.extends,
            )
    for source, target in extends_extra:
        if source in by_name:
            current = by_name[source]
            by_name[source] = UseCase(
                name=current.name,
                actors=current.actors,
                includes=current.includes,
                extends=tuple(dict.fromkeys((*current.extends, target))),
            )
    ordered = tuple(by_name[case.name] for case in cases)
    return DiagramSpec(system=system, actors=actors, use_cases=ordered)


def to_plantuml(spec: DiagramSpec) -> str:
    aliases: dict[str, str] = {}
    used: set[str] = set()
    for actor in spec.actors:
        aliases[f"actor:{actor.name}"] = _alias("A", actor.name, used)
    for case in spec.use_cases:
        aliases[f"uc:{case.name}"] = _alias("UC", case.name, used)

    lines = [
        "@startuml",
        "!pragma layout smetana",
        "skinparam shadowing false",
        "skinparam actorBorderColor #1B365D",
        "skinparam actorFontColor #1E232D",
        "skinparam usecaseBackgroundColor #FFFFFF",
        "skinparam usecaseBorderColor #1B365D",
        "skinparam usecaseFontColor #1E232D",
        "skinparam rectangleBorderColor #1B365D",
        "skinparam rectangleBackgroundColor #F8FAFC",
        "skinparam arrowColor #1B365D",
        "",
    ]
    left = [actor for actor in spec.actors if actor.side != "right"]
    right = [actor for actor in spec.actors if actor.side == "right"]
    for actor in left:
        lines.append(
            f"actor {_quote(actor.name)} as {aliases[f'actor:{actor.name}']}"
        )
    if left:
        lines.append("")
    lines.append(f"rectangle {_quote(spec.system)} {{")
    for case in spec.use_cases:
        lines.append(
            f"  usecase {_quote(case.name)} as {aliases[f'uc:{case.name}']}"
        )
    lines.append("}")
    if right:
        lines.append("")
    for actor in right:
        lines.append(
            f"actor {_quote(actor.name)} as {aliases[f'actor:{actor.name}']}"
        )
    lines.append("")

    actor_side = {actor.name: actor.side for actor in spec.actors}
    for case in spec.use_cases:
        uc_alias = aliases[f"uc:{case.name}"]
        for actor_name in case.actors:
            actor_key = f"actor:{actor_name}"
            if actor_key not in aliases:
                continue
            actor_alias = aliases[actor_key]
            if actor_side.get(actor_name) == "right":
                lines.append(f"{uc_alias} <-- {actor_alias}")
            else:
                lines.append(f"{actor_alias} --> {uc_alias}")
        for included in case.includes:
            other = aliases.get(f"uc:{included}")
            if other:
                lines.append(f"{uc_alias} ..> {other} : <<include>>")
        for base in case.extends:
            other = aliases.get(f"uc:{base}")
            if other:
                lines.append(f"{uc_alias} ..> {other} : <<extend>>")

    lines.append("")
    lines.append("@enduml")
    lines.append("")
    return "\n".join(lines)


def _parse_actors(raw: object) -> tuple[Actor, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    sides: dict[str, str] = {}
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            side = str(item.get("side") or "").strip().lower()
            if side in {"left", "right"}:
                sides[name] = side
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    if not unique:
        return ()
    assigned: list[Actor] = []
    for index, name in enumerate(unique):
        if name in sides:
            side = sides[name]
        elif len(unique) == 1:
            side = "left"
        elif index == 0:
            side = "left"
        elif index == len(unique) - 1:
            side = "right"
        else:
            side = "left" if index % 2 == 0 else "right"
        assigned.append(Actor(name=name, side=side))
    return tuple(assigned)


def _parse_use_case(item: object, actor_names: tuple[str, ...]) -> UseCase | None:
    if isinstance(item, str):
        name = item.strip()
        return UseCase(name=name, actors=actor_names, includes=(), extends=()) if name else None
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    linked_raw = item.get("actors")
    if isinstance(linked_raw, list):
        linked = tuple(str(actor).strip() for actor in linked_raw if str(actor).strip())
    elif linked_raw is None:
        linked = ()
    else:
        linked = actor_names
    includes = _name_list(item.get("includes") or item.get("include"))
    extends = _name_list(item.get("extends") or item.get("extend"))
    return UseCase(name=name, actors=linked, includes=includes, extends=extends)


def _name_list(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str) and raw.strip():
        return (raw.strip(),)
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("to") or "").strip()
            if name:
                names.append(name)
    return tuple(dict.fromkeys(names))


def _parse_relations(raw: object) -> list[tuple[str, str]]:
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = str(item.get("from") or item.get("source") or "").strip()
        target = str(item.get("to") or item.get("target") or "").strip()
        if source and target:
            pairs.append((source, target))
    return pairs


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _alias(prefix: str, name: str, used: set[str]) -> str:
    slug = _ALIAS_RE.sub("_", name).strip("_") or "X"
    if slug[0].isdigit():
        slug = f"{prefix}_{slug}"
    candidate = slug
    n = 2
    while candidate in used:
        candidate = f"{slug}_{n}"
        n += 1
    used.add(candidate)
    return candidate


def _expected_jar_sha256() -> str | None:
    if not VENDOR_MANIFEST.is_file():
        return None
    try:
        payload = json.loads(VENDOR_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return None
    info = files.get(JAR_REL)
    if not isinstance(info, dict):
        return None
    digest = info.get("sha256")
    return str(digest) if digest else None


def _require_jar() -> Path:
    if not VENDOR_JAR.is_file():
        raise SystemExit(
            f"Missing {JAR_REL}. This JAR is tracked in git — restore it from "
            "the repo (see vendor/README.md)."
        )
    expected = _expected_jar_sha256()
    actual = hashlib.sha256(VENDOR_JAR.read_bytes()).hexdigest()
    if expected and actual != expected:
        raise SystemExit(
            f"{JAR_REL} checksum mismatch.\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}\n"
            "Restore the committed JAR or update vendor/manifest.json."
        )
    return VENDOR_JAR


def _java_bin() -> str:
    java = shutil.which("java")
    if java:
        return java
    home = os.environ.get("JAVA_HOME", "").strip()
    if home:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(
        "Java is required to render the use-case diagram from "
        f"{JAR_REL}. Install a JRE, put java on PATH, then re-run "
        "python manage.py usecase."
    )


def _run_plantuml(puml: Path, dest: Path) -> None:
    jar = _require_jar()
    java = _java_bin()
    cmd = [
        java,
        "-Djava.awt.headless=true",
        "-jar",
        str(jar),
        "-charset",
        "UTF-8",
        "-tpng",
        "-Playout=smetana",
        "-o",
        str(dest.parent.resolve()),
        str(puml.resolve()),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            timeout=120,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("PlantUML timed out rendering the use-case diagram.") from exc
    except OSError as exc:
        raise SystemExit(f"Failed to launch Java/PlantUML: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        raise SystemExit(f"PlantUML failed:\n{detail}")
    produced = dest.parent / (puml.stem + ".png")
    if produced != dest:
        if not produced.is_file():
            raise SystemExit(f"PlantUML did not write {produced.as_posix()}.")
        shutil.move(str(produced), str(dest))
    elif not dest.is_file():
        raise SystemExit(f"PlantUML did not write {dest.as_posix()}.")
