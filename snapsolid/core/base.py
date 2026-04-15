"""Abstract interfaces for each pipeline step."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepResult:
    """Result of a pipeline step."""
    success: bool
    output_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PipelineStep(ABC):
    """Base interface for each pipeline step."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Step name."""

    @abstractmethod
    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Execute the step."""

    def validate_input(self, input_path: Path) -> bool:
        """Verify that the input is valid."""
        return input_path.exists()


class IngestStep(PipelineStep):
    """Interface for the ingest module (photos/video → images)."""

    @abstractmethod
    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Extract frames from video or copy photos to working directory."""


class ReconstructionStep(PipelineStep):
    """Interface for the 3D reconstruction module (SfM + dense)."""

    @abstractmethod
    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Reconstruct 3D mesh from images."""


class CleaningStep(PipelineStep):
    """Interface for the mesh cleaning module."""

    @abstractmethod
    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Clean and repair the mesh to make it printable."""


class ExportStep(PipelineStep):
    """Interface for the export module (mesh → printable STL)."""

    @abstractmethod
    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Export the mesh as print-ready STL."""
