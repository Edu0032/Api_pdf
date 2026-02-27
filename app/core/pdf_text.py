import io
from typing import List
import pdfplumber


def extract_pages_text(pdf_bytes: bytes, start_1based: int, end_1based: int) -> List[str]:
    """Extrai texto página a página no intervalo [start,end] (1-based)."""
    if start_1based < 1 or end_1based < 1:
        raise ValueError("Páginas devem ser 1-based e >= 1")
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        n = len(pdf.pages)
        if end_1based > n:
            raise ValueError(f"PDF tem {n} páginas, mas você pediu até {end_1based}")
        out = []
        for idx in range(start_1based - 1, end_1based):
            t = pdf.pages[idx].extract_text() or ""
            out.append(t)
        return out


def normalize_lines(text: str) -> List[str]:
    """Normaliza espaços e remove linhas vazias."""
    lines = []
    for raw in text.splitlines():
        s = " ".join(raw.strip().split())
        if s:
            lines.append(s)
    return lines