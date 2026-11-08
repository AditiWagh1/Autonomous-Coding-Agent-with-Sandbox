"""
agent/languages.py - Languages the agent can write and run. Add a new entry to support another one
(the sandbox image in docker/Dockerfile.sandbox must contain its compiler/interpreter).
"""

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class Language:
    key: str
    label: str
    filename: str
    fences: Tuple[str, ...]      # markdown fence tags the model may use
    run_template: str            # shell template; {src} = source path, {bin} = binary path
    hint: str                    # extra guidance for the system prompt
    supports_packages: bool = False


LANGUAGES: Dict[str, Language] = {
    "python": Language(
        "python", "Python 3.11", "main.py", ("python", "py", "python3"),
        "python -u {src}",
        "Python 3.11. Only the standard library is available unless you request packages.",
        supports_packages=True,
    ),
    "c": Language(
        "c", "C (gcc, C11)", "main.c", ("c",),
        "gcc -O2 -std=c11 -Wall -o {bin} {src} -lm && {bin}",
        "C11 compiled with gcc (-lm is linked). Include every header you use. Return 0 from main.",
    ),
    "cpp": Language(
        "cpp", "C++ (g++, C++17)", "main.cpp", ("cpp", "c++", "cc", "cxx"),
        "g++ -O2 -std=c++17 -Wall -o {bin} {src} && {bin}",
        "C++17 compiled with g++. Include every header you use. Return 0 from main.",
    ),
}


def get_language(key: str) -> Language:
    try:
        return LANGUAGES[(key or "python").lower()]
    except KeyError:
        raise ValueError(f"Unsupported language {key!r}. Choose from: {', '.join(LANGUAGES)}")


def shell_command(lang: Language, src: str, bin_path: str = "/tmp/prog") -> str:
    return lang.run_template.format(src=src, bin=bin_path)
