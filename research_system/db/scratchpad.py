"""Data models for the research system scratchpad."""
from typing import List
from pydantic import BaseModel, Field
from ..core.schemas import SourceCitation



 
class ResearchScratchpad(BaseModel):
    """Container for research artifacts during system orchestration."""
    hypothesis: str = Field(description="Current research thesis statement")
    key_findings: List[str] = Field(
        description="Distilled, source-backed facts as bullet points"
    )
    verified_sources: List[SourceCitation] = Field(
        description="Authoritative citations for key findings"
    )
    unresolved_gaps: List[str] = Field(
        description="Missing information identified for gap-fillers"
    )

    def read_section(self, section_name: str) -> str:
        """Read content from a specific research section."""
        # Implementation reads from file system or database
        return f"Content from {section_name} section"

    def write_section(
        self, section_name: str, content: str, append: bool = True
    ) -> None:
        """Write content to a specific research section.

        Args:
            section_name: Name of the section to write to
            content: Content to write
            append: If True, append to existing content; otherwise replace.
        """
        # Implementation writes to file or database
        mode = "append" if append else "replace"
        print(f"Writing to {section_name}: {content} (mode: {mode})")

    def to_prompt_context(self) -> str:
        """Format scratchpad content for LLM prompt context."""
        context = [
            "RESEARCH SCRATCHPAD CONTEXT:",
            f"Hypothesis: {self.hypothesis}",
            "",
            "KEY FINDINGS:",
        ] + self.key_findings + [
            "",
            "VERIFIED SOURCES:",
        ] + [f"- [{s.id}] {s.title} ({s.url})" for s in self.verified_sources] + [
            "",
            "UNRESOLVED GAPS:",
        ] + self.unresolved_gaps + [
            "",
            "Use this context to guide research direction and identify gaps.",
        ]
        return "\n".join(context)