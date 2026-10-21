"""Export docs/report.md to a PDF with the student's name and ID on the cover."""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fpdf import FPDF, FontFace, TextStyle
from fpdf.html import HTML2FPDF
from PIL import Image

from app.skill_integrity import IntegrityResult, require_clean, stamp_markdown
from app.transcript_export import (
    appendix_markdown,
    export_project_transcripts,
    merge_transcripts_section,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = REPO_ROOT / "docs" / "report.md"
DEFAULT_PDF = REPO_ROOT / "docs" / "report.pdf"
DEFAULT_JUDGE = REPO_ROOT / "docs" / "judge.md"
COMPETENCY_HEADING = "## Competency (student-judge)"
USECASE_HEADING = "## Use case diagram"
USECASE_IMAGE_REL = "diagrams/use-case.png"
USECASE_IMAGE_MD = f"![Use case diagram]({USECASE_IMAGE_REL})"
_USECASE_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?:docs/)?diagrams/use-case\.png\)",
    re.IGNORECASE,
)
_USECASE_SECTION_RE = re.compile(
    r"^## Use case diagram\n.*?(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)

NAVY = (27, 54, 93)
NAVY_HEX = "#1B365D"
GOLD = (196, 163, 90)
ZEBRA = "#F4F7FA"
HIGHLIGHT = "#E7EEF6"
MUTED = (90, 96, 110)

# A4 width 210mm minus left/right margins (16mm). fpdf2 HTML <img width> is in
# points and then divided by pdf.k, so 178 here would render at ~63mm.
_CONTENT_WIDTH_MM = 178.0
_MAX_IMAGE_HEIGHT_MM = 175.0
_PT_PER_MM = 72 / 25.4

_JUDGE_COMMENT_RE = re.compile(
    r"<!-- student-judge:competency(?:\n.*?)?-->\n?",
    re.DOTALL,
)
_JUDGE_SECTION_RE = re.compile(
    r"^## Competency \(student-judge\)\n.*?(?=^## Skill integrity|\Z)",
    re.MULTILINE | re.DOTALL,
)

_FONT_CANDIDATES = (
    (
        REPO_ROOT / "vendor" / "fonts" / "DejaVuSans.ttf",
        REPO_ROOT / "vendor" / "fonts" / "DejaVuSans-Bold.ttf",
        REPO_ROOT / "vendor" / "fonts" / "DejaVuSans-Oblique.ttf",
        REPO_ROOT / "vendor" / "fonts" / "DejaVuSans-BoldOblique.ttf",
    ),
    (
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\ariali.ttf"),
        Path(r"C:\Windows\Fonts\arialbi.ttf"),
    ),
    (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
    ),
    (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    ),
)


class ReportHTML2FPDF(HTML2FPDF):
    """fpdf2 ignores <font color> inside <th>/<td>; force white heading text."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if tag == "table" and self.table is not None:
            self.table._headings_style = FontFace(emphasis="BOLD", color="#FFFFFF")


class ReportPDF(FPDF):
    HTML2FPDF_CLASS = ReportHTML2FPDF
    incomplete = False

    def header(self) -> None:
        if self.page_no() == 1:
            self.set_fill_color(*NAVY)
            self.rect(0, 0, self.w, 36, "F")
            self.set_fill_color(*GOLD)
            self.rect(0, 36, self.w, 2.2, "F")
            self.set_text_color(255, 255, 255)
            self.set_font("Body", "B", 20)
            self.set_xy(self.l_margin, 9)
            self.cell(self.epw, 10, "COMP 3613 Assignment 1")
            self.set_font("Body", "", 11)
            self.set_xy(self.l_margin, 20)
            subtitle = "Individual report (incomplete)" if self.incomplete else "Individual report"
            self.cell(self.epw, 8, subtitle)
            self.set_text_color(0, 0, 0)
            self.set_y(48)
            return
        # Draw in the top margin so body text (t_margin) starts below the rule.
        self.set_y(10)
        self.set_font("Body", "", 9)
        self.set_text_color(*NAVY)
        self.cell(self.epw, 6, "COMP 3613 Assignment 1", new_x="LMARGIN", new_y="NEXT")
        self.ln(1.5)
        self.set_draw_color(*GOLD)
        self.set_line_width(0.5)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_text_color(0, 0, 0)
        self.set_y(self.t_margin)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_draw_color(220, 224, 230)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-12)
        self.set_font("Body", "", 8)
        self.set_text_color(*MUTED)
        self.cell(self.epw, 8, f"{self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def export_report(
    *,
    name: str,
    student_id: str,
    source: Path | None = None,
    output: Path | None = None,
) -> Path:
    """Write a PDF from the markdown report. Returns the output path."""
    src = source or DEFAULT_REPORT
    dest = output or DEFAULT_PDF
    clean_name = name.strip()
    clean_id = student_id.strip()
    if not clean_name or not clean_id:
        raise SystemExit("Report export needs both --name and --id.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(
            "# COMP 3613 Assignment 1\n\nDraft. Export is allowed at any stage.\n",
            encoding="utf-8",
        )
        print(f"Created {src} (empty draft).")

    integrity = require_clean()
    transcripts = export_project_transcripts()
    markdown = merge_judge_section(src.read_text(encoding="utf-8"))
    markdown = ensure_usecase_in_report(markdown, report_path=src)
    markdown = merge_transcripts_section(markdown, transcripts)
    markdown = stamp_markdown(markdown, integrity)
    src.write_text(markdown, encoding="utf-8")
    if COMPETENCY_HEADING not in markdown:
        print(
            "No judge report yet. Ask the Guide to build or export the report "
            "so it runs student-judge and writes docs/judge.md."
        )
    app_url, logins, missing = _marker_access(markdown)
    dest.parent.mkdir(parents=True, exist_ok=True)

    pdf = _new_pdf(bool(missing))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        cover = _cover_html(clean_name, clean_id, app_url, logins, integrity, missing)
        body = _markdown_to_html(markdown, src.parent, Path(tmp))
        appendix = _markdown_to_html(
            appendix_markdown(transcripts),
            src.parent,
            Path(tmp),
        )
        _write_report_html(pdf, cover + body)
        if appendix.strip():
            pdf.add_page()
            _write_report_html(pdf, appendix)
    pdf.output(dest)
    print(f"Wrote {dest}")
    if missing:
        print("Incomplete draft. Missing: " + "; ".join(missing))
        print("Re-export later when those sections are filled. Full marks still need a live URL.")
    return dest


def ensure_usecase_in_report(
    markdown: str,
    *,
    report_path: Path | None = None,
) -> str:
    """Keep the use-case PNG linked with a path relative to docs/report.md.

    Regenerates docs/diagrams/use-case.png when the JSON spec exists.
    Rewrites broken ``docs/diagrams/...`` links to ``diagrams/use-case.png``.
    """
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    spec = REPO_ROOT / "docs" / "diagrams" / "use-case.json"
    png = REPO_ROOT / "docs" / "diagrams" / "use-case.png"
    if spec.is_file():
        try:
            from app.usecase_diagram import render_usecase_png

            render_usecase_png(spec_path=spec, output=png)
            print(f"Rendered use-case diagram -> {png.as_posix()}")
        except SystemExit as exc:
            print(f"Use-case diagram not rendered: {exc}")
        except Exception as exc:  # noqa: BLE001 — export should continue
            print(f"Use-case diagram not rendered: {exc}")

    if _USECASE_IMAGE_RE.search(text):
        text = _USECASE_IMAGE_RE.sub(USECASE_IMAGE_MD, text, count=1)
    elif _USECASE_SECTION_RE.search(text):
        text = _USECASE_SECTION_RE.sub(
            f"{USECASE_HEADING}\n\n{USECASE_IMAGE_MD}\n\n",
            text,
            count=1,
        )
    else:
        # Insert before Model diagram when possible.
        if re.search(r"^## Model diagram\s*$", text, re.MULTILINE):
            text = re.sub(
                r"^## Model diagram\s*$",
                f"{USECASE_HEADING}\n\n{USECASE_IMAGE_MD}\n\n## Model diagram",
                text,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            text = text.rstrip() + f"\n\n{USECASE_HEADING}\n\n{USECASE_IMAGE_MD}\n"

    # Verify the relative link resolves from the report file.
    base = (report_path or DEFAULT_REPORT).parent
    resolved = _resolve(USECASE_IMAGE_REL, base)
    if resolved is None and not png.is_file():
        print(
            "Warning: use-case PNG missing. Write docs/diagrams/use-case.json "
            "and run python manage.py usecase (or re-export after Phase 2)."
        )
    elif resolved is not None:
        print(f"Use-case diagram linked in report -> {USECASE_IMAGE_REL}")
    if not text.endswith("\n"):
        text += "\n"
    return text


def merge_judge_section(markdown: str, judge_path: Path | None = None) -> str:
    """Replace or insert the competency section from docs/judge.md when present."""
    sidecar = judge_path or DEFAULT_JUDGE
    if sidecar.is_file():
        body = sidecar.read_text(encoding="utf-8").strip()
        if body:
            print(f"Appended judge report from {sidecar}")
            return replace_competency_section(markdown, body)
    return markdown.replace("\r\n", "\n").replace("\r", "\n")


def replace_competency_section(markdown: str, judge_markdown: str) -> str:
    """Write the judge scorecard under Competency, before Skill integrity."""
    body = judge_markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    body = re.sub(
        r"^#\s+student-judge competency report\s*",
        "",
        body,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
    section = (
        "<!-- student-judge:competency -->\n"
        f"{COMPETENCY_HEADING}\n\n"
        "Filled by Guide from the student-judge run when this report was built.\n\n"
        f"{body}\n"
    )
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _JUDGE_COMMENT_RE.sub("", text, count=1)
    if _JUDGE_SECTION_RE.search(text):
        text = _JUDGE_SECTION_RE.sub(section.rstrip() + "\n\n", text, count=1)
    elif re.search(r"^## Skill integrity\s*$", text, re.MULTILINE):
        text = re.sub(
            r"^## Skill integrity\s*$",
            section + "\n## Skill integrity",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        text = text.rstrip() + "\n\n" + section
    if not text.endswith("\n"):
        text += "\n"
    return text


def _new_pdf(incomplete: bool) -> ReportPDF:
    pdf = ReportPDF(format="A4")
    pdf.incomplete = incomplete
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(16, 28, 16)
    regular, bold, italic, bold_italic = _fonts()
    if regular is None:
        raise SystemExit(
            "No Unicode font found (looked for Arial or DejaVu). "
            "Cannot embed the student name safely."
        )
    pdf.add_font("Body", "", str(regular))
    pdf.add_font("Body", "B", str(bold or regular))
    if italic is not None:
        pdf.add_font("Body", "I", str(italic))
    if bold_italic is not None:
        pdf.add_font("Body", "BI", str(bold_italic))
    pdf.add_page()
    return pdf


def _fonts() -> tuple[Path | None, Path | None, Path | None, Path | None]:
    for regular, bold, italic, bold_italic in _FONT_CANDIDATES:
        if regular.is_file():
            return (
                regular,
                bold if bold.is_file() else regular,
                italic if italic.is_file() else None,
                bold_italic if bold_italic.is_file() else None,
            )
    return None, None, None, None


def _write_report_html(pdf: ReportPDF, markup: str) -> None:
    pdf.write_html(
        markup,
        font_family="Body",
        table_line_separators=True,
        warn_on_tags_not_matching=False,
        tag_styles={
            "h1": TextStyle(
                font_family="Body",
                font_style="B",
                color=NAVY_HEX,
                font_size_pt=16,
                t_margin=3,
                b_margin=0.05,
            ),
            "h2": TextStyle(
                font_family="Body",
                font_style="B",
                color=NAVY_HEX,
                font_size_pt=13,
                t_margin=3.2,
                b_margin=0.05,
            ),
            "h3": TextStyle(
                font_family="Body",
                font_style="B",
                color=NAVY_HEX,
                font_size_pt=11,
                t_margin=3.0,
                b_margin=0.04,
            ),
            "p": TextStyle(font_family="Body", font_size_pt=10.5, t_margin=0.7, b_margin=0.9),
            "li": TextStyle(
                font_family="Body",
                font_size_pt=10.5,
                l_margin=5,
                t_margin=0.15,
                b_margin=0.15,
            ),
            "ul": TextStyle(t_margin=0, b_margin=0),
            "ol": TextStyle(t_margin=0, b_margin=0),
            "pre": TextStyle(
                font_family="Body",
                font_size_pt=8.5,
                color="#333333",
                t_margin=3,
                b_margin=3,
            ),
            "blockquote": TextStyle(
                font_family="Body",
                font_size_pt=10,
                color="#4B5563",
                l_margin=8,
                t_margin=3,
                b_margin=3,
            ),
        },
    )


def _cover_html(
    name: str,
    student_id: str,
    app_url: str,
    logins: str,
    integrity: IntegrityResult,
    missing: list[str],
) -> str:
    login_html = " · ".join(
        html_lib.escape(line.strip()) for line in logins.splitlines() if line.strip()
    ) or "Not yet"
    root = integrity.root
    if len(root) > 28:
        root = f"{root[:12]}…{root[-10:]}"
    integrity_value = html_lib.escape(f"{integrity.status.upper()}. Root {root}")
    rows = [
        ("Student name", html_lib.escape(name), False),
        ("Student ID", html_lib.escape(student_id), False),
        ("Deployed app", html_lib.escape(app_url), False),
        ("Logins", login_html, False),
        ("Skill integrity", integrity_value, not integrity.ok),
    ]
    cells = []
    for index, (label, value, fail) in enumerate(rows):
        if fail:
            bg = "#FDECEC"
        elif index % 2 == 0:
            bg = ZEBRA
        else:
            bg = "#FFFFFF"
        cells.append(
            f'<tr bgcolor="{bg}">'
            f'<td align="left">{html_lib.escape(label)}</td>'
            f'<td align="left">{value}</td>'
            f"</tr>"
        )
    missing_note = ""
    if missing:
        missing_note = (
            '<p><font color="#9A3412"><b>Incomplete draft.</b> Missing: '
            + html_lib.escape("; ".join(missing))
            + "</font></p>"
        )
    return f"""
<table width="100%" border="1" cellpadding="5">
<thead><tr bgcolor="{NAVY_HEX}">
<th width="28%" align="left"><font color="#FFFFFF"><b>Field</b></font></th>
<th width="72%" align="left"><font color="#FFFFFF"><b>Value</b></font></th>
</tr></thead>
<tbody>{"".join(cells)}</tbody>
</table>
{missing_note}
<p><font size="2" color="#5A6070">The YouTube video must show this name and must not show or say this ID. App logins belong in this report. Database passwords do not.</font></p>
"""


def _section(markdown: str, *titles: str) -> str:
    wanted = {title.lower() for title in titles}
    capture = False
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            if capture:
                break
            heading = line.lstrip("#").strip().lower()
            capture = heading in wanted
            continue
        if capture:
            lines.append(line)
    return "\n".join(lines).strip()


def _marker_access(markdown: str) -> tuple[str, str, list[str]]:
    missing: list[str] = []
    deployed = _section(markdown, "deployed app", "deployed app link", "deploy url")
    urls = re.findall(r"https?://[^\s)]+", deployed)
    app_url = next((url for url in urls if "localhost" not in url and "127.0.0.1" not in url), "")
    if not app_url:
        app_url = "Not yet"
        missing.append("public Render URL")

    logins = _section(markdown, "logins", "user logins")
    login_lines: list[str] = []
    for line in logins.splitlines():
        cleaned = re.sub(r"^[-*]\s+", "", line.strip())
        if (
            cleaned
            and not cleaned.startswith("Markers ")
            and not cleaned.startswith("Every ")
            and not cleaned.startswith("Starter ")
        ):
            login_lines.append(cleaned)
    if len(login_lines) < 2:
        login_text = "\n".join(login_lines) if login_lines else "Not yet"
        missing.append("marker logins (at least two accounts)")
    else:
        login_text = "\n".join(login_lines)
    return app_url, login_text, missing


def _markdown_to_html(markdown: str, base: Path, tmp: Path) -> str:
    parts: list[str] = []
    blocks = _blocks(markdown)
    i = 0
    diagram_n = 0
    skip_first_h1 = True
    while i < len(blocks):
        kind = blocks[i][0]
        if kind == "heading":
            level, text = blocks[i][1], blocks[i][2]
            if skip_first_h1 and level == 1:
                skip_first_h1 = False
                i += 1
                continue
            skip_first_h1 = False
            tag = f"h{min(level, 3)}"
            lower = text.lower()
            if "competency" in lower:
                parts.append('<p style="break-before: page"></p>')
            parts.append(f"<{tag}>{_inline_html(text)}</{tag}>")
        elif kind == "paragraph":
            parts.append(f'<p style="line-height: 1.35">{_inline_html(blocks[i][1])}</p>')
        elif kind == "list":
            items: list[str] = []
            while i < len(blocks) and blocks[i][0] == "list":
                items.append("• " + _inline_html(blocks[i][1]))
                i += 1
            # fpdf2 <ul> inserts a 0-height dummy paragraph; the first <li> then
            # sits on the heading line. A single paragraph keeps bullets under the title.
            parts.append(
                '<p style="line-height: 1.35">' + "<br>".join(items) + "</p>"
            )
            continue
        elif kind == "ordered":
            items = []
            while i < len(blocks) and blocks[i][0] == "ordered":
                items.append(
                    f"{html_lib.escape(blocks[i][1])}. {_inline_html(blocks[i][2])}"
                )
                i += 1
            parts.append(
                '<p style="line-height: 1.35">' + "<br>".join(items) + "</p>"
            )
            continue
        elif kind == "table":
            parts.append(_table_html(blocks[i][1]))
        elif kind == "image":
            path = _resolve(blocks[i][1], base)
            if path is None:
                parts.append(
                    "<p><i>Image not in workspace yet: "
                    + _inline_html(blocks[i][1])
                    + "</i></p>"
                )
            else:
                parts.append(_image_html(path))
        elif kind == "mermaid":
            diagram_n += 1
            png = tmp / f"diagram-{diagram_n}.png"
            if _render_mermaid(blocks[i][1], png):
                parts.append(_image_html(png))
            else:
                parts.append(
                    "<p><i>Mermaid source (install Node.js and re-run export to render the image):</i></p>"
                    f"<pre>{html_lib.escape(blocks[i][1])}</pre>"
                )
                print(
                    "warning: Mermaid diagram left as source. "
                    "Install Node.js, run npm ci, then re-export (pinned mermaid-cli in package-lock.json)."
                )
        elif kind == "code":
            parts.append(f"<pre>{html_lib.escape(blocks[i][1])}</pre>")
        i += 1
    return "".join(parts)


def _image_html(path: Path) -> str:
    """Embed an image at nearly full content width so UML/wireframes stay readable."""
    try:
        with Image.open(path) as image:
            px_w, px_h = image.size
    except OSError:
        px_w, px_h = 1600, 900
    if px_w <= 0:
        px_w, px_h = 1600, 900
    aspect = px_h / px_w
    width_mm = _CONTENT_WIDTH_MM
    height_mm = width_mm * aspect
    if height_mm > _MAX_IMAGE_HEIGHT_MM:
        height_mm = _MAX_IMAGE_HEIGHT_MM
        width_mm = height_mm / aspect
    width_pt = width_mm * _PT_PER_MM
    height_pt = height_mm * _PT_PER_MM
    src = html_lib.escape(str(path))
    return f'<img src="{src}" width="{width_pt:.1f}" height="{height_pt:.1f}">'


def _table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    rows = _compact_scorecard(rows)
    widths = _col_widths(rows[0])
    header = [cell.strip() or "Item" for cell in rows[0]]
    header = [_display_header(cell) for cell in header]
    body = rows[1:]
    labels = [cell.strip().lower() for cell in header]
    center = {
        i
        for i, label in enumerate(labels)
        if label in {"score", "score / 4", "avg", "in avg"}
    }
    head_cells = []
    for index, cell in enumerate(header):
        width = widths[index] if index < len(widths) else None
        width_attr = f' width="{width}%"' if width else ""
        align = "center" if index in center else "left"
        head_cells.append(
            f'<th{width_attr} align="{align}">'
            f'<font color="#FFFFFF"><b>{_cell_html(cell)}</b></font></th>'
        )
    body_rows = []
    for row_i, row in enumerate(body):
        key = row[0].lower() if row else ""
        if "overall" in key or "impression" in key:
            bg = HIGHLIGHT
        elif row_i % 2 == 1:
            bg = ZEBRA
        else:
            bg = "#FFFFFF"
        tds = []
        for index, cell in enumerate(row):
            align = "center" if index in center else "left"
            tds.append(f'<td align="{align}">{_cell_html(cell)}</td>')
        body_rows.append(f'<tr bgcolor="{bg}">{"".join(tds)}</tr>')
    return (
        '<table width="100%" border="1" cellpadding="3">'
        f'<thead><tr bgcolor="{NAVY_HEX}">{"".join(head_cells)}</tr></thead>'
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )


def _compact_scorecard(rows: list[list[str]]) -> list[list[str]]:
    """Drop Max when every scored cell is /4, and put that on the Score header."""
    header = [cell.strip() for cell in rows[0]]
    labels = [cell.lower() for cell in header]
    if "score" not in labels or "max" not in labels:
        return rows
    max_i = labels.index("max")
    score_i = labels.index("score")
    skip = {"", "—", "-", "n/a"}
    values = {
        row[max_i].strip().lower()
        for row in rows[1:]
        if max_i < len(row)
    } - skip
    if values and values != {"4"}:
        return rows
    header[score_i] = "Score / 4"
    header.pop(max_i)
    body = []
    for row in rows[1:]:
        new_row = list(row)
        if max_i < len(new_row):
            new_row.pop(max_i)
        body.append(new_row)
    return [header, *body]


def _display_header(cell: str) -> str:
    aliases = {
        "in avg": "Avg",
        "score / 4": "Score / 4",
        "scoreable max": "Max points",
        "awarded total": "Awarded",
        "overall (avg of scored)": "Overall",
        "impression mark": "Impression",
        "metrics on rubric": "Metrics",
        "n/a (excluded)": "N/A",
        "metrics scored": "Scored",
    }
    return aliases.get(cell.strip().lower(), cell)


def _col_widths(header: list[str]) -> list[int]:
    labels = [cell.strip().lower() for cell in header]
    count = len(labels)
    if count == 2:
        return [38, 62]
    joined = " ".join(labels)
    if count == 3 and "phase" in joined:
        return [12, 16, 72]
    if "evidence" in joined:
        if count == 5:
            return [8, 17, 14, 8, 53]
        if count >= 6:
            return _fit_widths([7, 18, 10, 8, 8, 49], count)
    share = 100 // max(count, 1)
    widths = [share] * count
    widths[-1] = 100 - share * (count - 1)
    return widths


def _fit_widths(widths: list[int], count: int) -> list[int]:
    if len(widths) == count:
        return widths
    if len(widths) > count:
        kept = widths[: count - 1]
        kept.append(100 - sum(kept))
        return kept
    extra = count - len(widths)
    share = max(widths[-1] // (extra + 1), 8)
    tail = [share] * extra
    widths[-1] = 100 - sum(widths[:-1]) - sum(tail)
    return widths + tail


def _cell_html(text: str) -> str:
    """Table cells cannot mix nested tags with trailing text in fpdf2."""
    escaped = html_lib.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"\1", escaped)
    escaped = re.sub(r"`([^`]+)`", r"\1", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", escaped)
    return escaped or " "


def _inline_html(text: str) -> str:
    escaped = html_lib.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def _blocks(markdown: str) -> list[tuple]:
    lines = markdown.splitlines()
    blocks: list[tuple] = []
    i = 0
    paragraph: list[str] = []

    def flush() -> None:
        text = " ".join(part.strip() for part in paragraph if part.strip())
        paragraph.clear()
        if text:
            blocks.append(("paragraph", text))

    while i < len(lines):
        line = lines[i]
        if "<!--" in line:
            flush()
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            i += 1
            continue
        if line.startswith("```"):
            flush()
            lang = line[3:].strip().lower()
            i += 1
            body: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            text = "\n".join(body).strip()
            blocks.append(("mermaid" if lang == "mermaid" else "code", text))
            i += 1
            continue
        image = re.fullmatch(r"!\[[^\]]*\]\(([^)]+)\)", line.strip())
        if image:
            flush()
            blocks.append(("image", image.group(1).strip()))
            i += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush()
            blocks.append(("heading", len(heading.group(1)), heading.group(2).strip()))
            i += 1
            continue
        if line.strip().startswith("|"):
            flush()
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                raw = lines[i].strip()
                if not re.match(r"^\|[\s:|-]+\|$", raw):
                    cells = [cell.strip() for cell in raw.strip("|").split("|")]
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(("table", rows))
            continue
        if re.match(r"^\s*[-*]\s+", line):
            flush()
            blocks.append(("list", re.sub(r"^\s*[-*]\s+", "", line)))
            i += 1
            continue
        ordered = re.match(r"^\s*(\d+)\.\s+(.*)$", line)
        if ordered:
            flush()
            blocks.append(("ordered", ordered.group(1), ordered.group(2)))
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        paragraph.append(line)
        i += 1
    flush()
    return blocks


def _resolve(raw: str, base: Path) -> Path | None:
    """Resolve an image path from report-relative, repo-root, or docs/-prefixed forms."""
    candidates: list[Path] = []
    path = Path(raw)
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(base / path)
        candidates.append(REPO_ROOT / path)
        # report.md lives in docs/; authors sometimes write docs/diagrams/... by mistake
        posix = path.as_posix().lstrip("./")
        if posix.startswith("docs/"):
            candidates.append(base / posix[len("docs/") :])
            candidates.append(REPO_ROOT / posix)
        elif base.name == "docs":
            candidates.append(REPO_ROOT / "docs" / posix)

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            key = candidate.resolve()
        except OSError:
            key = candidate
        if key in seen:
            continue
        seen.add(key)
        if key.is_file():
            return key
    return None


def _mermaid_cli_spec() -> str:
    package_json = REPO_ROOT / "package.json"
    if package_json.is_file():
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        deps = payload.get("dependencies") if isinstance(payload, dict) else None
        if isinstance(deps, dict):
            version = str(deps.get("@mermaid-js/mermaid-cli") or "").strip()
            if version:
                return f"@mermaid-js/mermaid-cli@{version}"
    return "@mermaid-js/mermaid-cli"


def _render_mermaid(source: str, png: Path) -> bool:
    mmd = png.with_suffix(".mmd")
    mmd.write_text(source, encoding="utf-8")
    commands: list[list[str]] = []
    local_name = "mmdc.cmd" if os.name == "nt" else "mmdc"
    local = REPO_ROOT / "node_modules" / ".bin" / local_name
    if local.is_file():
        commands.append([str(local), "-i", str(mmd), "-o", str(png), "-b", "white"])
    mmdc = shutil.which("mmdc")
    if mmdc:
        commands.append([mmdc, "-i", str(mmd), "-o", str(png), "-b", "white"])
    npx = shutil.which("npx")
    if npx:
        commands.append(
            [npx, "--yes", _mermaid_cli_spec(), "-i", str(mmd), "-o", str(png), "-b", "white"]
        )
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, timeout=180, capture_output=True)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        if png.is_file():
            return True
    return False
