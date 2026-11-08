"""
agent/parsing.py - Split an LLM reply into runnable code and requested packages.
"""

import re
from typing import List, Tuple

from .languages import get_language

_FENCE_RE = re.compile(r"```([A-Za-z0-9_.+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_REQ_LANGS = {"requirements", "requirements.txt", "pip"}


def parse_response(raw: str, language: str = "python") -> Tuple[str, List[str]]:
    """Return (code, requirements). code is "" if no matching code block was found.
    requirements are only returned for Python."""
    lang = get_language(language)
    code_tags = set(lang.fences) | {""}
    code_blocks: List[str] = []
    reqs: List[str] = []
    for tag, body in _FENCE_RE.findall(raw or ""):
        tag = tag.lower()
        if tag in _REQ_LANGS:
            if lang.supports_packages:
                for line in body.splitlines():
                    line = line.split("#")[0].strip()
                    if line and line not in reqs:
                        reqs.append(line)
        elif tag in code_tags:
            code_blocks.append(body)
    code = max(code_blocks, key=len).strip() if code_blocks else ""
    return code, reqs
