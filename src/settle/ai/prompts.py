"""Versioned prompts, stored as Markdown files next to this module.

Each file starts with a ``version: <id>`` line. The version is saved with every AI output, so
any recommendation can be traced to the exact prompt that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}"


@cache
def load_prompt(name: str) -> Prompt:
    raw = resources.files("settle.ai").joinpath("prompts", f"{name}.md").read_text("utf-8")
    first, _, rest = raw.partition("\n")
    if not first.startswith("version:"):
        raise ValueError(f"Prompt {name} must start with a 'version:' line")
    return Prompt(name=name, version=first.removeprefix("version:").strip(), text=rest.strip())
