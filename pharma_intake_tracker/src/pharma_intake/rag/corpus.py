"""Knowledge corpus: synthetic SOPs (markdown) plus past safety reports from SQLite."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from ..schemas import ReportType, StoredReport
from .chunker import TokenCounter, split_windows

_SOP_ID_RE = re.compile(r"^(SOP-\d{3})")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    source_type: str          # "sop" | "report"
    title: str
    section: str
    text: str
    token_count: int

    @property
    def citation(self) -> str:
        if self.source_type == "sop":
            return f"{self.doc_id} — {self.section}"
        return f"{self.doc_id} (past report)"


def parse_sop(path: Path) -> Tuple[str, str, List[Tuple[str, str]]]:
    """Return (doc_id, title, [(section_heading, section_text), ...])."""
    match = _SOP_ID_RE.match(path.stem)
    doc_id = match.group(1) if match else path.stem
    title = doc_id
    sections: List[Tuple[str, str]] = []
    heading, buffer = "Overview", []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            continue  # document-level disclaimer banner, not retrievable content
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            if any(b.strip() for b in buffer):
                sections.append((heading, "\n".join(buffer).strip()))
            heading, buffer = line[3:].strip(), []
        else:
            buffer.append(line)
    if any(b.strip() for b in buffer):
        sections.append((heading, "\n".join(buffer).strip()))
    return doc_id, title, sections


def sop_chunks(sop_dir: Path, counter: TokenCounter) -> List[Chunk]:
    chunks: List[Chunk] = []
    for path in sorted(sop_dir.glob("SOP-*.md")):
        doc_id, title, sections = parse_sop(path)
        for s_idx, (heading, body) in enumerate(sections, start=1):
            text = f"{title} — {heading}\n{body}"
            for w_idx, window in enumerate(split_windows(text, counter), start=1):
                chunks.append(
                    Chunk(f"{doc_id}#{s_idx}.{w_idx}", doc_id, "sop", title, heading, window.text, window.token_count)
                )
    return chunks


def report_text(report: StoredReport) -> str:
    text = (
        f"Past report {report.ref_id} ({report.report_type.value}), status {report.status.value}. "
        f"Product: {report.product_name}. Batch/lot: {report.batch_lot_number}. "
        f"Received {report.date_received.isoformat()} by {report.intake_channel.value} "
        f"from a {report.reporter_type.value}. Description: {report.description}"
    )
    if report.report_type is ReportType.ADVERSE_EVENT and report.event_description:
        onset = report.event_onset_date.isoformat() if report.event_onset_date else "not recorded"
        text += f" Event: {report.event_description} Onset date: {onset}."
    return text


def report_chunks(reports: Iterable[StoredReport], counter: TokenCounter) -> List[Chunk]:
    chunks: List[Chunk] = []
    for report in sorted(reports, key=lambda r: r.ref_id):
        for w_idx, window in enumerate(split_windows(report_text(report), counter), start=1):
            chunks.append(
                Chunk(
                    f"{report.ref_id}#{w_idx}",
                    report.ref_id,
                    "report",
                    f"{report.report_type.value} — {report.product_name}",
                    report.status.value,
                    window.text,
                    window.token_count,
                )
            )
    return chunks


def build_chunks(sop_dir: Path, reports: Sequence[StoredReport], counter: TokenCounter) -> List[Chunk]:
    return sop_chunks(sop_dir, counter) + report_chunks(reports, counter)
