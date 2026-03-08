import re
from typing import Optional

_PTBR_RE = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|-?\d+(?:,\d+)?$")


def parse_ptbr_number(value: str) -> Optional[float]:
    """Converte '1.234,56' em 1234.56. Retorna None se não parecer número."""
    if value is None:
        return None
    s = value.strip().replace("R$", "").replace(" ", "")
    if not s:
        return None
    if not _PTBR_RE.match(s):
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None