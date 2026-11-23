"""
agent/artifacts.py - Sanity checks for files the generated program wrote to /output.
Catches "fake" files such as SVG text saved as .png, which would otherwise count as a success.
"""

from typing import Dict, List

_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n", "PNG"),
    ".jpg": (b"\xff\xd8\xff", "JPEG"),
    ".jpeg": (b"\xff\xd8\xff", "JPEG"),
    ".gif": (b"GIF8", "GIF"),
    ".pdf": (b"%PDF", "PDF"),
}


def validate_artifacts(artifacts: Dict[str, bytes]) -> List[str]:
    """Return a list of human-readable problems (empty list = all files look fine)."""
    problems: List[str] = []
    for name, data in artifacts.items():
        low = name.lower()
        if len(data) == 0:
            problems.append(f"{name} is empty")
            continue
        for ext, (magic, label) in _SIGNATURES.items():
            if low.endswith(ext) and not data.startswith(magic):
                problems.append(f"{name} is not a valid {label} file (it starts with {data[:12]!r}); "
                                f"write a real {label} image or use the correct extension")
        if low.endswith(".svg") and b"<svg" not in data[:2000].lower():
            problems.append(f"{name} is not a valid SVG file")
    return problems
