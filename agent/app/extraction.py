import io

from pypdf import PdfReader


def pdf_bytes_to_text(data: bytes) -> str:
    """Extract text from a PDF, prefixing each page with a page marker."""
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"[第{i}页]\n{text.strip()}")
    return "\n\n".join(parts)


def source_bytes_to_text(data: bytes) -> str:
    if data[:4] == b"%PDF":
        return pdf_bytes_to_text(data)
    return data.decode("utf-8", errors="replace")


def split_sections(text: str, max_chars: int = 6000) -> list[dict[str, object]]:
    """Split full text into readable sections for full-coverage reading.

    Paragraph-grouped, char-based chunking: keeps paragraphs intact and
    targets ~max_chars per section so each read_section call fits in context.
    """
    paragraphs = text.split("\n\n")
    sections: list[str] = []
    buffer: list[str] = []
    size = 0
    for para in paragraphs:
        if size + len(para) > max_chars and buffer:
            sections.append("\n\n".join(buffer))
            buffer = []
            size = 0
        buffer.append(para)
        size += len(para) + 2
    if buffer:
        sections.append("\n\n".join(buffer))
    return [{"index": i, "text": s} for i, s in enumerate(sections)]
