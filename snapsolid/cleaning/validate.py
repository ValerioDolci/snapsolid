"""Final validation: is the mesh printable?"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import trimesh

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """3D printing validation result."""
    is_printable: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"=== 3D Print Validation ===",
            f"Printable: {'YES' if self.is_printable else 'NO'}",
            "",
        ]
        for check, passed in self.checks.items():
            status = "PASS" if passed else "FAIL"
            lines.append(f"  [{status}] {check}")
        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"  WARN: {w}")
        return "\n".join(lines)


class MeshValidator:
    """Verify that a mesh is ready for 3D printing."""

    def validate(self, mesh: trimesh.Trimesh) -> ValidationResult:
        """Run all printability checks."""
        result = ValidationResult()

        result.checks["watertight"] = bool(mesh.is_watertight)
        result.checks["positive_volume"] = self._check_volume(mesh)
        result.checks["no_degenerate_faces"] = self._check_no_degenerate(mesh)
        result.checks["no_duplicate_faces"] = self._check_no_duplicates(mesh)
        result.checks["single_component"] = self._check_single_component(mesh)
        result.checks["consistent_normals"] = self._check_normals(mesh)

        # Warnings (non-blocking)
        self._check_face_count(mesh, result)
        self._check_thin_walls(mesh, result)

        result.is_printable = all(result.checks.values())

        logger.info("Validation complete: %s",
                     "PRINTABLE" if result.is_printable else "NOT PRINTABLE")
        return result

    def _check_volume(self, mesh: trimesh.Trimesh) -> bool:
        """Volume must be positive (normals facing outward)."""
        try:
            return float(mesh.volume) > 0
        except Exception:
            return False

    def _check_no_degenerate(self, mesh: trimesh.Trimesh) -> bool:
        """No faces with area ~0."""
        return bool(np.all(mesh.area_faces > 1e-10))

    def _check_no_duplicates(self, mesh: trimesh.Trimesh) -> bool:
        """No duplicate faces."""
        sorted_faces = np.sort(mesh.faces, axis=1)
        unique = np.unique(sorted_faces, axis=0)
        return len(unique) == len(sorted_faces)

    def _check_single_component(self, mesh: trimesh.Trimesh) -> bool:
        """The mesh must be a single connected component."""
        components = mesh.split(only_watertight=False)
        return len(components) == 1

    def _check_normals(self, mesh: trimesh.Trimesh) -> bool:
        """Normals must be consistent."""
        try:
            return bool(mesh.is_watertight) and float(mesh.volume) > 0
        except Exception:
            return False

    def _check_face_count(self, mesh: trimesh.Trimesh, result: ValidationResult) -> None:
        """Warning if too many faces for the slicer."""
        if len(mesh.faces) > 500_000:
            result.warnings.append(
                f"Very dense mesh ({len(mesh.faces):,} faces). "
                "May slow down the slicer. Consider simplify=True."
            )

    def _check_thin_walls(self, mesh: trimesh.Trimesh, result: ValidationResult) -> None:
        """Warning for walls too thin (difficult to print)."""
        try:
            if mesh.is_watertight:
                vol = float(mesh.volume)
                area = float(mesh.area)
                if area > 0:
                    thickness_proxy = vol / area
                    if thickness_proxy < 0.1:
                        result.warnings.append(
                            f"Possible thin walls (vol/area={thickness_proxy:.4f}). "
                            "Verify with slicer."
                        )
        except Exception:
            pass
