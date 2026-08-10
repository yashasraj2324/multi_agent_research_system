"""Typed working-memory model for research scratchpad state."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..core.schemas import SourceCitation


class ResearchScratchpad(BaseModel):
    """Structured scratchpad persisted under the MemoryStore ``scratchpad`` key."""

    hypothesis: str = Field(default="", description="Current research thesis statement.")
    key_findings: list[str] = Field(
        default_factory=list,
        description="Distilled, source-backed facts as bullet points.",
    )
    verified_sources: list[SourceCitation] = Field(
        default_factory=list,
        description="Authoritative citations for key findings.",
    )
    unresolved_gaps: list[str] = Field(
        default_factory=list,
        description="Missing information identified for gap-fillers.",
    )
    sections: dict[str, Any] = Field(
        default_factory=dict,
        description="Named free-form scratchpad sections used by agent tools.",
    )

    @classmethod
    def from_raw(cls, raw: Any) -> "ResearchScratchpad":
        """Build a scratchpad from stored JSON, a dict, or the old raw-section format."""
        if raw is None:
            return cls()
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                return cls(sections={"notes": raw})
        if not isinstance(raw, dict):
            return cls(sections={"value": raw})

        model_fields = set(cls.model_fields)
        if any(key in model_fields for key in raw):
            return cls.model_validate(raw)
        return cls(sections=dict(raw))

    @classmethod
    def from_memory(cls, memory: Any) -> "ResearchScratchpad":
        """Load scratchpad state from a MemoryStore-like object."""
        if memory is None or not hasattr(memory, "read"):
            return cls()
        return cls.from_raw(memory.read("scratchpad"))

    def write_to_memory(self, memory: Any) -> None:
        """Persist scratchpad state to a MemoryStore-like object."""
        if memory is not None and hasattr(memory, "write"):
            memory.write("scratchpad", self.model_dump(mode="json"))

    def read_section(self, section_name: str) -> Any:
        """Read either a typed field or a named free-form section."""
        if hasattr(self, section_name):
            return getattr(self, section_name)
        return self.sections.get(section_name, "")

    def write_section(
        self, section_name: str, content: Any, append: bool = False
    ) -> None:
        """Write either a typed field or a named free-form section."""
        if section_name in {"hypothesis", "key_findings", "verified_sources", "unresolved_gaps"}:
            self._write_typed_field(section_name, content, append=append)
            return

        if append and section_name in self.sections and self.sections[section_name]:
            self.sections[section_name] = f"{self.sections[section_name]}\n{content}"
        else:
            self.sections[section_name] = content

    def _write_typed_field(self, section_name: str, content: Any, *, append: bool) -> None:
        current = getattr(self, section_name)
        if isinstance(current, list):
            values = content if isinstance(content, list) else [content]
            setattr(self, section_name, [*current, *values] if append else values)
            return
        if append and current:
            setattr(self, section_name, f"{current}\n{content}")
        else:
            setattr(self, section_name, str(content))

    def to_prompt_context(self) -> str:
        """Format scratchpad content for LLM prompt context."""
        context = [
            "RESEARCH SCRATCHPAD CONTEXT:",
            f"Hypothesis: {self.hypothesis or 'Not set'}",
            "",
            "KEY FINDINGS:",
            *[f"- {item}" for item in self.key_findings],
            "",
            "VERIFIED SOURCES:",
            *[f"- [{s.id}] {s.title} ({s.url})" for s in self.verified_sources],
            "",
            "UNRESOLVED GAPS:",
            *[f"- {gap}" for gap in self.unresolved_gaps],
        ]
        if self.sections:
            context.extend(["", "FREE-FORM SECTIONS:"])
            for name, value in self.sections.items():
                rendered = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
                context.append(f"- {name}: {rendered}")
        return "\n".join(context)
