"""Markdown + PDF export helpers."""
from __future__ import annotations

import io
import re

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from ..core.schemas import ResearchReport


def report_to_markdown(query: str, report: ResearchReport) -> str:
    lines: list[str] = []
    lines.append(f"# {report.title}\n")
    lines.append(f"_Original query: {query}_\n")
    lines.append("## Executive summary\n")
    lines.append(report.executive_summary.strip() + "\n")
    for sec in report.sections:
        lines.append(f"## {sec.heading}\n")
        lines.append(sec.body_markdown.strip() + "\n")
    if report.all_sources:
        lines.append("## Sources\n")
        for i, s in enumerate(report.all_sources, start=1):
            title = s.title or s.url
            lines.append(f"[{i}] [{title}]({s.url})")
    return "\n".join(lines)


def _inline_md(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', text)
    text = re.sub(
        r"\[(.+?)\]\((.+?)\)",
        r'<link href="\2" color="blue"><u>\1</u></link>',
        text,
    )
    return text


def _md_to_paragraphs(text: str, style: ParagraphStyle) -> list[Paragraph]:
    """Convert simple Markdown to ReportLab paragraphs."""
    paragraphs: list[Paragraph] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        if all(line.lstrip().startswith(("- ", "* ")) for line in block.splitlines()):
            for line in block.splitlines():
                item = line.lstrip()[2:]
                paragraphs.append(Paragraph(f"• {_inline_md(item)}", style))
            continue
        paragraphs.append(Paragraph(_inline_md(block.replace("\n", "<br/>")), style))
    return paragraphs


def report_to_pdf_bytes(query: str, report: ResearchReport) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=report.title,
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], spaceAfter=8)
    body = ParagraphStyle("body", parent=styles["BodyText"], leading=15, spaceAfter=8)
    meta = ParagraphStyle("meta", parent=styles["Italic"], textColor="#666666")

    story = [
        Paragraph(_inline_md(report.title), h1),
        Paragraph(f"Original query: {_inline_md(query)}", meta),
        Spacer(1, 10),
        Paragraph("Executive summary", h2),
    ]
    story.extend(_md_to_paragraphs(report.executive_summary, body))
    for sec in report.sections:
        story.append(Spacer(1, 6))
        story.append(Paragraph(_inline_md(sec.heading), h2))
        story.extend(_md_to_paragraphs(sec.body_markdown, body))

    if report.all_sources:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Sources", h2))
        for i, s in enumerate(report.all_sources, start=1):
            title = s.title or s.url
            story.append(
                Paragraph(
                    f'[{i}] <link href="{s.url}" color="blue"><u>{_inline_md(title)}</u></link>',
                    body,
                )
            )

    doc.build(story)
    return buf.getvalue()
