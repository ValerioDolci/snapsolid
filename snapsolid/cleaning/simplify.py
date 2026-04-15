"""Mesh simplification and smoothing."""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

import pymeshlab
import trimesh

from ..config import CleaningConfig

logger = logging.getLogger(__name__)


class MeshSimplifier:
    """Mesh decimation and smoothing."""

    def __init__(self, config: CleaningConfig):
        self.config = config

    def simplify(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Apply simplification and/or smoothing."""
        ms = self._trimesh_to_meshlab(mesh)

        if self.config.simplify:
            ms = self._decimate(ms)

        if self.config.smooth:
            ms = self._smooth(ms)

        return self._meshlab_to_trimesh(ms)

    def _decimate(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Reduce face count while preserving shape."""
        current_faces = ms.current_mesh().face_number()
        target = self.config.target_faces

        if current_faces <= target:
            logger.info("Mesh already below target (%d <= %d), skip decimation",
                        current_faces, target)
            return ms

        logger.info("Decimation: %d -> %d faces...", current_faces, target)
        try:
            ms.apply_filter(
                "meshing_decimation_quadric_edge_collapse",
                targetfacenum=target,
                preserveboundary=True,
                preservenormal=True,
                preservetopology=True,
                qualitythr=0.5,
            )
            final = ms.current_mesh().face_number()
            logger.info("Decimation complete: %d faces", final)
        except Exception as e:
            logger.warning("Decimation error: %s", e)
        return ms

    def _smooth(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Apply smoothing to the mesh.

        If method is 'adaptive', uses curvature-weighted smoothing:
        smooths flat areas, preserves edges and details.
        """
        method = self.config.smooth_method
        iterations = self.config.smooth_iterations

        logger.info("Smoothing %s (%d iterations)...", method, iterations)
        try:
            if method == "adaptive":
                # Adaptive smoothing: two-step smoothing preserves features
                # better than standard Taubin — uses separate step and smoothing
                # for edges and flat surfaces
                ms.apply_filter(
                    "apply_coord_two_steps_smoothing",
                    stepsmoothnum=iterations,
                    normalthr=60.0,  # angle threshold: edges > 60° preserved
                    selected=False,
                )
                logger.info("Adaptive two-step smoothing (angle threshold 60°) applied")
            elif method == "taubin":
                ms.apply_filter(
                    "apply_coord_taubin_smoothing",
                    stepsmoothnum=iterations,
                    lambda_=0.5,
                    mu=-0.53,
                )
            elif method == "laplacian":
                ms.apply_filter(
                    "apply_coord_laplacian_smoothing",
                    stepsmoothnum=iterations,
                )
            else:
                logger.warning("Unknown smoothing method: %s", method)
        except Exception as e:
            logger.warning("Smoothing error: %s", e)
        return ms

    def _trimesh_to_meshlab(self, mesh: trimesh.Trimesh) -> pymeshlab.MeshSet:
        """Convert trimesh -> MeshSet via temporary file."""
        ms = pymeshlab.MeshSet()
        with NamedTemporaryFile(suffix=".ply", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            mesh.export(str(tmp_path))
            ms.load_new_mesh(str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)
        return ms

    def _meshlab_to_trimesh(self, ms: pymeshlab.MeshSet) -> trimesh.Trimesh:
        """Convert MeshSet -> trimesh via temporary file."""
        with NamedTemporaryFile(suffix=".ply", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            ms.save_current_mesh(str(tmp_path))
            result = trimesh.load(str(tmp_path), force="mesh")
        finally:
            tmp_path.unlink(missing_ok=True)
        return result
