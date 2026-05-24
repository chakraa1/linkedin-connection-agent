"""
PDF Profile Extractor — extracts and structures text from LinkedIn profile PDFs.
Requires: pip install pdfplumber
"""
import re
from pathlib import Path

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False


def extract_pdf_text(pdf_path: str) -> str:
    if not _HAS_PDFPLUMBER:
        return "[pdfplumber not installed — run: pip install pdfplumber]"
    path = Path(pdf_path)
    if not path.exists():
        return f"[PDF not found: {pdf_path}]"
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() for page in pdf.pages if page.extract_text()]
    return "\n\n".join(pages)


def extract_profile_sections(pdf_text: str) -> dict:
    sections: dict = {
        "experience": [],
        "education": [],
        "skills": [],
        "summary": "",
        "certifications": [],
        "raw": pdf_text,
    }
    _HEADERS = {
        "experience": r"^(experience|work experience)$",
        "education": r"^education$",
        "skills": r"^(skills|top skills)$",
        "summary": r"^(summary|about|profile)$",
        "certifications": r"^(licenses & certifications|certifications)$",
    }

    current = None
    buffer: list[str] = []

    def flush(section: str, buf: list[str]) -> None:
        if not buf:
            return
        if section == "summary":
            sections["summary"] = " ".join(buf)
        elif section in ("experience", "education", "certifications"):
            sections[section] = buf[:5]
        elif section == "skills":
            sections["skills"] = buf[:10]

    for line in pdf_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        matched = next(
            (s for s, pat in _HEADERS.items() if re.match(pat, stripped.lower())), None
        )
        if matched:
            flush(current, buffer)
            current = matched
            buffer = []
        elif current:
            buffer.append(stripped)

    flush(current, buffer)
    return sections
