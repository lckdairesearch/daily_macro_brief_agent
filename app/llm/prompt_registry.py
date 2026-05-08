"""Load versioned markdown prompts from app/llm/prompts."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    """Prompt text plus metadata for run auditing."""

    name: str
    path: Path
    text: str


def _resolve_prompt_path(name: str) -> tuple[str, Path]:
    safe_name = name.removesuffix(".md")
    if not safe_name or safe_name.startswith(("/", "\\")) or ".." in Path(safe_name).parts:
        raise ValueError(f"Invalid prompt name: {name}")

    path = (PROMPT_ROOT / f"{safe_name}.md").resolve()
    prompt_root = PROMPT_ROOT.resolve()
    if not path.is_relative_to(prompt_root):
        raise ValueError(f"Invalid prompt path: {name}")
    return safe_name, path


@lru_cache(maxsize=64)
def load_prompt(name: str) -> PromptTemplate:
    """Return a prompt template by stable name, e.g. ``brief_writer``."""
    safe_name, path = _resolve_prompt_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {safe_name}")
    return PromptTemplate(name=safe_name, path=path, text=path.read_text(encoding="utf-8"))


def clear_prompt_cache() -> None:
    """Clear cached prompt reads; useful for tests and local prompt editing."""
    load_prompt.cache_clear()
