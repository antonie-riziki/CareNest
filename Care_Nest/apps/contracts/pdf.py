from __future__ import annotations

from io import BytesIO


def _safe(text) -> str:
    raw = str(text or "").replace("\r", "")
    return raw.encode("latin-1", "replace").decode("latin-1")


def _pdf_escape(text: str) -> str:
    return _safe(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, width: int = 92) -> list[str]:
    lines: list[str] = []
    for paragraph in _safe(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= width:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines or [""]


def render_completion_pdf(snapshot: dict, *, share_url: str = "") -> bytes:
    job = snapshot.get("job") or {}
    parties = snapshot.get("parties") or {}
    pay = snapshot.get("pay") or {}
    terms = snapshot.get("terms") or {}
    rating = snapshot.get("rating") or {}
    settlement = snapshot.get("settlement") or {}
    blocks = [
        "CareNest completion report",
        f"Contract #{snapshot.get('engagement_id')}  ·  {snapshot.get('status')}",
        f"Generated {snapshot.get('generated_at', '')}",
        "",
        "Contract",
        f"Role: {job.get('title')} ({job.get('type')})",
        f"Location: {job.get('location')}",
        f"Schedule: {job.get('schedule')}",
        f"Employer: {parties.get('employer')}",
        f"Worker: {parties.get('worker')}",
        f"Rate: {pay.get('display')}",
        f"Platform fee: {pay.get('platform_fee')}",
        f"Worker receives: {pay.get('worker_receives')}",
        f"Employer rating of work: {rating.get('label')}",
        f"Settlement: {settlement.get('status')} {settlement.get('reference')}".strip(),
        "",
        "Employer terms",
        terms.get("employer") or "None recorded.",
        "",
        "Worker terms",
        terms.get("worker") or "None recorded.",
        f"Hours note: {terms.get('hours') or '—'}",
        "",
        "Activities and attendance",
        f"Total logged time: {snapshot.get('total_minutes') or 0} minutes",
    ]
    for shift in snapshot.get("shifts") or []:
        blocks.append(
            f"- In {shift.get('checked_in_at')}  Out {shift.get('checked_out_at') or 'open'}  "
            f"({shift.get('minutes') or 0} min)  rating {shift.get('employer_rating') or 'n/a'}"
        )
        if shift.get("work_summary"):
            blocks.append(f"  Work: {shift['work_summary']}")
        if shift.get("employer_note"):
            blocks.append(f"  Employer: {shift['employer_note']}")
    if snapshot.get("invoices"):
        blocks.append("")
        blocks.append("Invoices")
        for inv in snapshot["invoices"]:
            blocks.append(f"- {inv.get('number')}  {inv.get('status')}  {inv.get('currency')} {inv.get('amount')}")
    if share_url:
        blocks.extend(["", f"Share link: {share_url}"])

    lines: list[str] = []
    for block in blocks:
        lines.extend(_wrap(block))

    pages: list[list[str]] = []
    page_size = 48
    for i in range(0, len(lines), page_size):
        pages.append(lines[i : i + page_size])
    if not pages:
        pages = [["CareNest completion report"]]

    objects = ["dummy"]
    kids = []
    content_ids = []
    for page_lines in pages:
        y = 780
        commands = ["BT", "/F1 11 Tf"]
        for line in page_lines:
            commands.append(f"1 0 0 1 48 {y} Tm ({_pdf_escape(line)}) Tj")
            y -= 14
        commands.append("ET")
        stream = "\n".join(commands).encode("latin-1", "replace")
        content_id = len(objects)
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        )
        content_ids.append(content_id)
        page_id = len(objects)
        objects.append(None)  # placeholder filled after pages object exists
        kids.append(page_id)

    font_id = len(objects)
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objects)
    kids_refs = " ".join(f"{kid} 0 R" for kid in kids)
    objects.append(f"<< /Type /Pages /Kids [{kids_refs}] /Count {len(kids)} >>".encode("latin-1"))
    for index, page_id in enumerate(kids):
        objects[page_id] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_ids[index]} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode("latin-1")
    catalog_id = len(objects)
    objects.append(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))

    buffer = BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj_id, body in enumerate(objects):
        if obj_id == 0:
            continue
        offsets.append(buffer.tell())
        if isinstance(body, str):
            body = body.encode("latin-1", "replace")
        buffer.write(f"{obj_id} 0 obj\n".encode("latin-1"))
        buffer.write(body)
        buffer.write(b"\nendobj\n")
    xref = buffer.tell()
    count = len(objects)
    buffer.write(f"xref\n0 {count}\n".encode("latin-1"))
    buffer.write(b"0000000000 65535 f \n")
    for obj_id in range(1, count):
        buffer.write(f"{offsets[obj_id]:010d} 00000 n \n".encode("latin-1"))
    buffer.write(
        f"trailer\n<< /Size {count} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    )
    return buffer.getvalue()
