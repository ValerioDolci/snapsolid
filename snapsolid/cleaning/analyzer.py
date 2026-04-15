"""Mesh analysis: diagnose issues before cleaning."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

logger = logging.getLogger(__name__)


@dataclass
class MeshReport:
    """Diagnostic report for a mesh."""
    # Base geometry
    vertex_count: int = 0
    face_count: int = 0
    edge_count: int = 0
    bounding_box_size: tuple[float, float, float] = (0.0, 0.0, 0.0)
    volume: float | None = None

    # State
    is_watertight: bool = False
    is_manifold: bool = False
    has_degenerate_faces: bool = False
    has_duplicate_faces: bool = False

    # Issues found
    non_manifold_edges: int = 0
    non_manifold_vertices: int = 0
    holes_count: int = 0
    degenerate_faces: int = 0
    duplicate_faces: int = 0
    components_count: int = 0
    unreferenced_vertices: int = 0

    # Recommendations
    issues: list[str] = field(default_factory=list)

    @property
    def is_printable(self) -> bool:
        """Is the mesh ready for 3D printing?"""
        return (
            self.is_watertight
            and self.is_manifold
            and self.degenerate_faces == 0
            and self.duplicate_faces == 0
            and self.components_count <= 1
        )

    def summary(self) -> str:
        """Human-readable report summary."""
        lines = [
            f"=== Mesh Report ===",
            f"Vertices: {self.vertex_count:,}",
            f"Faces: {self.face_count:,}",
            f"Bounding box: {self.bounding_box_size[0]:.2f} x {self.bounding_box_size[1]:.2f} x {self.bounding_box_size[2]:.2f}",
            f"Components: {self.components_count}",
            f"",
            f"Watertight: {'YES' if self.is_watertight else 'NO'}",
            f"Manifold: {'YES' if self.is_manifold else 'NO'}",
            f"Printable: {'YES' if self.is_printable else 'NO'}",
        ]
        if self.volume is not None:
            lines.append(f"Volume: {self.volume:.4f}")

        if self.issues:
            lines.append("")
            lines.append("Issues found:")
            for issue in self.issues:
                lines.append(f"  - {issue}")

        return "\n".join(lines)


class MeshAnalyzer:
    """Analyze a mesh and produce a diagnostic report."""

    def analyze(self, mesh: trimesh.Trimesh) -> MeshReport:
        """Analyze the mesh and return a report."""
        report = MeshReport()

        # Base geometry
        report.vertex_count = len(mesh.vertices)
        report.face_count = len(mesh.faces)
        report.edge_count = len(mesh.edges_unique)
        extents = mesh.bounding_box.extents
        report.bounding_box_size = (float(extents[0]), float(extents[1]), float(extents[2]))

        # State
        report.is_watertight = bool(mesh.is_watertight)
        report.is_manifold = self._check_manifold(mesh)

        if report.is_watertight:
            try:
                report.volume = float(mesh.volume)
            except Exception:
                report.volume = None

        # Components
        components = mesh.split(only_watertight=False)
        report.components_count = len(components)

        # Degenerate faces (area ~0)
        face_areas = mesh.area_faces
        report.degenerate_faces = int(np.sum(face_areas < 1e-10))
        report.has_degenerate_faces = report.degenerate_faces > 0

        # Duplicate faces
        report.duplicate_faces = self._count_duplicate_faces(mesh)
        report.has_duplicate_faces = report.duplicate_faces > 0

        # Unreferenced vertices
        referenced = np.unique(mesh.faces.flatten())
        report.unreferenced_vertices = report.vertex_count - len(referenced)

        # Holes (boundary edges indicate open mesh)
        try:
            outline = mesh.outline()
            if outline is not None and hasattr(outline, "entities"):
                report.holes_count = len(outline.entities)
            else:
                report.holes_count = 0 if report.is_watertight else -1
        except Exception:
            report.holes_count = 0 if report.is_watertight else -1

        # Generate issues list
        report.issues = self._generate_issues(report)

        logger.info("Analysis complete: %d vertices, %d faces, %d issues",
                     report.vertex_count, report.face_count, len(report.issues))

        return report

    def _check_manifold(self, mesh: trimesh.Trimesh) -> bool:
        """Check if the mesh is manifold."""
        try:
            edges = mesh.edges_sorted
            unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
            return bool(mesh.is_watertight) or not np.any(counts > 1)
        except Exception:
            return False

    def _count_duplicate_faces(self, mesh: trimesh.Trimesh) -> int:
        """Count duplicate faces."""
        sorted_faces = np.sort(mesh.faces, axis=1)
        unique_faces = np.unique(sorted_faces, axis=0)
        return len(sorted_faces) - len(unique_faces)

    def _generate_issues(self, report: MeshReport) -> list[str]:
        """Generate list of issues found."""
        issues = []

        if not report.is_watertight:
            issues.append("Mesh not watertight (not closed)")
        if not report.is_manifold:
            issues.append("Mesh non-manifold (invalid topology)")
        if report.degenerate_faces > 0:
            issues.append(f"{report.degenerate_faces} degenerate faces (area ~0)")
        if report.duplicate_faces > 0:
            issues.append(f"{report.duplicate_faces} duplicate faces")
        if report.components_count > 1:
            issues.append(f"{report.components_count} disconnected components")
        if report.unreferenced_vertices > 0:
            issues.append(f"{report.unreferenced_vertices} unreferenced vertices")
        if report.holes_count > 0:
            issues.append(f"{report.holes_count} holes detected")

        return issues
