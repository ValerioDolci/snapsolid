"""Mesh cleaning orchestrator: analysis → repair → simplify → validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import trimesh

from ..config import CleaningConfig
from ..core.base import CleaningStep, StepResult
from .analyzer import MeshAnalyzer, MeshReport
from .repair import MeshRepairer
from .simplify import MeshSimplifier
from .validate import MeshValidator, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class CleaningResult:
    """Complete cleaning result."""
    report_before: MeshReport
    report_after: MeshReport
    validation: ValidationResult
    mesh: trimesh.Trimesh

    def summary(self) -> str:
        lines = [
            "=" * 50,
            "SNAPSOLID — Mesh Cleaning Complete",
            "=" * 50,
            "",
            "--- BEFORE ---",
            self.report_before.summary(),
            "",
            "--- AFTER ---",
            self.report_after.summary(),
            "",
            self.validation.summary(),
        ]
        return "\n".join(lines)


class MeshCleaner(CleaningStep):
    """Complete mesh cleaning orchestrator.

    Flow:
    1. Load mesh
    2. Analysis (issue report)
    3. Repair (fix topology, holes, normals)
    4. Simplify + smooth (optional)
    5. Re-analysis
    6. Printability validation
    7. Export
    """

    def __init__(self, config: CleaningConfig | None = None):
        self.config = config or CleaningConfig()
        self.analyzer = MeshAnalyzer()
        self.repairer = MeshRepairer(self.config)
        self.simplifier = MeshSimplifier(self.config)
        self.validator = MeshValidator()

    @property
    def name(self) -> str:
        return "mesh_cleaning"

    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Run the complete cleaning pipeline."""
        try:
            result = self.clean_file(input_path, output_path)
            # Success if: mesh improved (fewer issues) or printable
            # We don't require watertight because voxel patch destroys detail
            improved = len(result.report_after.issues) < len(result.report_before.issues)
            return StepResult(
                success=result.validation.is_printable or improved,
                output_path=output_path,
                metadata={
                    "faces_before": result.report_before.face_count,
                    "faces_after": result.report_after.face_count,
                    "is_printable": result.validation.is_printable,
                    "issues_before": len(result.report_before.issues),
                    "issues_after": len(result.report_after.issues),
                },
                warnings=result.validation.warnings,
            )
        except Exception as e:
            logger.error("Mesh cleaning error: %s", e)
            return StepResult(success=False, errors=[str(e)])

    def clean_file(self, input_path: Path, output_path: Path) -> CleaningResult:
        """Clean a mesh file and save the result."""
        logger.info("Loading mesh: %s", input_path)
        mesh = trimesh.load(str(input_path), force="mesh")

        result = self.clean_mesh(mesh)

        # Export
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.mesh.export(str(output_path))
        logger.info("Mesh saved: %s", output_path)

        return result

    def clean_mesh(self, mesh: trimesh.Trimesh) -> CleaningResult:
        """Clean a mesh in memory."""
        # 1. Initial analysis
        logger.info("--- Initial analysis ---")
        report_before = self.analyzer.analyze(mesh)
        logger.info("\n%s", report_before.summary())

        # 2. If already printable, skip (but apply simplify if requested)
        if report_before.is_printable and not self.config.simplify:
            logger.info("Mesh already printable, no repair needed")
            validation = self.validator.validate(mesh)
            return CleaningResult(
                report_before=report_before,
                report_after=report_before,
                validation=validation,
                mesh=mesh,
            )

        # 3. Repair
        logger.info("--- Repair ---")
        mesh = self.repairer.repair(mesh)

        # 4. Simplify + smooth
        if self.config.simplify or self.config.smooth:
            logger.info("--- Simplification/Smoothing ---")
            mesh = self.simplifier.simplify(mesh)

        # 5. Re-analysis
        logger.info("--- Post-repair analysis ---")
        report_after = self.analyzer.analyze(mesh)
        logger.info("\n%s", report_after.summary())

        # 6. Validation
        logger.info("--- Validation ---")
        validation = self.validator.validate(mesh)
        logger.info("\n%s", validation.summary())

        return CleaningResult(
            report_before=report_before,
            report_after=report_after,
            validation=validation,
            mesh=mesh,
        )
