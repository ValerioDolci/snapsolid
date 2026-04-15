"""Export cleaned mesh as printable STL + JSON report."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import trimesh

from ..core.base import ExportStep, StepResult

logger = logging.getLogger(__name__)


class STLExporter(ExportStep):
    """Export mesh as binary STL + validation report."""

    @property
    def name(self) -> str:
        return "stl_export"

    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Export mesh from input_path to output_path as STL.

        Optional kwargs:
            metadata: dict with metadata to save in JSON report
        """
        input_path = Path(input_path)
        output_path = Path(output_path)

        if not input_path.exists():
            return StepResult(success=False, errors=[f"Input not found: {input_path}"])

        try:
            mesh = trimesh.load(str(input_path), force="mesh")
        except Exception as e:
            return StepResult(success=False, errors=[f"Mesh loading error: {e}"])

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure .stl extension
        if output_path.suffix.lower() != ".stl":
            output_path = output_path.with_suffix(".stl")

        # Save binary STL
        try:
            mesh.export(str(output_path), file_type="stl")
        except Exception as e:
            return StepResult(success=False, errors=[f"STL export error: {e}"])

        size_mb = output_path.stat().st_size / (1024 * 1024)

        # JSON report alongside the STL
        report = {
            "file": output_path.name,
            "vertices": len(mesh.vertices),
            "faces": len(mesh.faces),
            "watertight": bool(mesh.is_watertight),
            "volume": float(mesh.volume) if mesh.is_watertight else None,
            "size_mb": round(size_mb, 2),
        }
        # Add extra metadata if provided
        extra = kwargs.get("metadata", {})
        if extra:
            report["pipeline"] = extra

        report_path = output_path.with_suffix(".json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        logger.info("Exported %s (%.1f MB, %d faces)", output_path.name, size_mb, len(mesh.faces))

        return StepResult(
            success=True,
            output_path=output_path,
            metadata=report,
        )
