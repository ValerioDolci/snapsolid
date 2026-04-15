"""Pipeline orchestrator — connects modules in sequence."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import PipelineConfig, CleaningConfig
from .core.base import StepResult

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of the complete pipeline."""
    success: bool = False
    steps: dict[str, StepResult] = field(default_factory=dict)
    output_path: Path | None = None
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "SNAPSOLID — Pipeline Complete",
            "=" * 60,
        ]
        for name, result in self.steps.items():
            status = "OK" if result.success else "FAIL"
            lines.append(f"  [{status}] {name}")
            if result.errors:
                for e in result.errors:
                    lines.append(f"       ! {e}")
            if result.warnings:
                for w in result.warnings:
                    lines.append(f"       ~ {w}")
            if result.metadata:
                for k, v in result.metadata.items():
                    if not isinstance(v, (dict, list)):
                        lines.append(f"       {k}: {v}")
        lines.append("")
        lines.append(f"Result: {'SUCCESS' if self.success else 'FAILED'}")
        lines.append(f"Time: {self.elapsed_seconds:.1f}s")
        if self.output_path:
            lines.append(f"Output: {self.output_path}")
        return "\n".join(lines)


class Pipeline:
    """Modular Snapsolid pipeline.

    Connects steps in sequence:
    Quality Gate → Reconstruction → Cleaning → Export

    Each step is optional and replaceable.
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or PipelineConfig()

    def run(self, input_path: Path, output_dir: Path, **kwargs) -> PipelineResult:
        """Run the complete pipeline: photos → STL.

        Args:
            input_path: directory with photos (JPEG/PNG)
            output_dir: output directory for all artifacts
            **kwargs:
                detail: Object Capture detail level (default: full)
                ordering: ordering (default: auto)
                sensitivity: Object Capture sensitivity (default: high)
                max_photos: max photos for quality gate subset (default: 0 = use all)
                skip_quality_gate: skip the quality gate
                skip_cleaning: skip mesh cleaning
                cleaning_preset: cleaning preset (gentle/standard/aggressive)
                planar_flatten: enable planar flattening (rectangular buildings)
                planar_angle_threshold: angle threshold for region growing (default: 15°)
                planar_min_region: minimum faces per planar region (default: 50)
                planar_strength: projection strength 0-1 (default: 0.7)
                decimate: enable decimation
                decimate_target: target faces after decimation (default: 1M)
                skip_base: skip the rectangular base
        """
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        result = PipelineResult()
        t0 = time.time()

        # --- Step 1: Quality Gate ---
        if not kwargs.get("skip_quality_gate", False):
            step_result = self._step_quality_gate(input_path, output_dir, **kwargs)
            result.steps["quality_gate"] = step_result
            if not step_result.success:
                result.errors.append("Quality gate failed")
                result.elapsed_seconds = time.time() - t0
                return result
            # Use the subset as input for reconstruction
            photo_input = step_result.output_path
        else:
            logger.info("Quality gate skipped")
            photo_input = input_path

        # --- Step 2: Reconstruction (Apple Object Capture) ---
        step_result = self._step_reconstruction_apple(photo_input, output_dir, **kwargs)
        result.steps["reconstruction"] = step_result
        if not step_result.success:
            result.errors.append("Reconstruction failed")
            result.elapsed_seconds = time.time() - t0
            return result
        mesh_path = step_result.output_path

        # If output is USDZ, convert to OBJ
        if mesh_path.suffix.lower() == ".usdz":
            step_result = self._step_usdz_convert(mesh_path, output_dir)
            result.steps["usdz_convert"] = step_result
            if not step_result.success:
                result.errors.append("USDZ conversion failed")
                result.elapsed_seconds = time.time() - t0
                return result
            mesh_path = step_result.output_path

        # --- Step 3: Cleaning ---
        if not kwargs.get("skip_cleaning", False):
            step_result = self._step_cleaning(mesh_path, output_dir, **kwargs)
            result.steps["cleaning"] = step_result
            if not step_result.success:
                result.errors.append("Mesh cleaning failed")
                result.elapsed_seconds = time.time() - t0
                return result
            mesh_path = step_result.output_path
        else:
            logger.info("Cleaning skipped")

        # --- Step 3b: Remove residual fragments ---
        step_result = self._step_remove_fragments(mesh_path, output_dir)
        if step_result.success and step_result.output_path:
            result.steps["remove_fragments"] = step_result
            mesh_path = step_result.output_path

        # --- Step 3c: Planar flattening (rectangular buildings) ---
        if kwargs.get("planar_flatten", False):
            step_result = self._step_planar_flatten(mesh_path, output_dir, **kwargs)
            if step_result.success and step_result.output_path:
                result.steps["planar_flatten"] = step_result
                mesh_path = step_result.output_path

        # --- Step 3d: Decimation ---
        if kwargs.get("decimate", False):
            step_result = self._step_decimate(mesh_path, output_dir, **kwargs)
            if step_result.success and step_result.output_path:
                result.steps["decimate"] = step_result
                mesh_path = step_result.output_path

        # --- Step 3e: Rectangular base (drone) ---
        if not kwargs.get("skip_base", False):
            step_result = self._step_rectangular_base(mesh_path, output_dir, **kwargs)
            if step_result.success and step_result.output_path:
                result.steps["rectangular_base"] = step_result
                mesh_path = step_result.output_path

        # --- Step 3f: Scale to mm ---
        scale_mm = kwargs.get("scale_to_mm", 0)
        if scale_mm and scale_mm > 0:
            step_result = self._step_scale(mesh_path, output_dir, scale_mm)
            if step_result.success and step_result.output_path:
                result.steps["scale"] = step_result
                mesh_path = step_result.output_path

        # --- Step 4: Export STL ---
        step_result = self._step_export(mesh_path, output_dir, result.steps)
        result.steps["export"] = step_result
        if step_result.success:
            result.success = True
            result.output_path = step_result.output_path

        result.elapsed_seconds = time.time() - t0

        # Save JSON report with full traceability
        report_path = output_dir / "pipeline_report.json"
        self._save_report(result, report_path, input_path=input_path, parameters=kwargs)

        logger.info("\n%s", result.summary())
        return result

    def _step_quality_gate(
        self, input_path: Path, output_dir: Path, **kwargs
    ) -> StepResult:
        """Step 1: Quality gate — analyze and filter photos."""
        logger.info("=" * 40)
        logger.info("STEP 1: Quality Gate")
        logger.info("=" * 40)

        from .ingest.quality_gate import QualityGate

        gate = QualityGate()
        report = gate.analyze(input_path)

        if report.passed == 0:
            return StepResult(
                success=False,
                errors=["No photos passed the quality gate"],
            )

        # Default: use all good photos (max_photos=0 → no cap)
        max_photos = kwargs.get("max_photos", 0)
        subset = gate.select_subset(input_path, report, max_photos=max_photos)

        # Copy/link selected photos to a subdirectory
        subset_dir = output_dir / "photos_selected"
        subset_dir.mkdir(parents=True, exist_ok=True)
        for fname in subset.files:
            src = input_path / fname
            dst = subset_dir / fname
            if not dst.exists():
                shutil.copy2(str(src), str(dst))

        logger.info(
            "Quality gate: %d/%d passed, subset: %d photos (mean overlap: %.0f)",
            report.passed, report.total, len(subset.files), subset.mean_overlap,
        )

        # Save report
        qg_report = {
            "total": report.total,
            "passed": report.passed,
            "rejected": report.rejected,
            "subset_size": len(subset.files),
            "mean_overlap": subset.mean_overlap,
            "min_overlap": subset.min_overlap,
            "thresholds": report.thresholds,
        }
        with open(output_dir / "quality_gate_report.json", "w") as f:
            json.dump(qg_report, f, indent=2)

        return StepResult(
            success=True,
            output_path=subset_dir,
            metadata={
                "total_photos": report.total,
                "passed": report.passed,
                "selected": len(subset.files),
                "mean_overlap": round(subset.mean_overlap, 1),
            },
        )

    def _step_reconstruction_apple(
        self, photo_dir: Path, output_dir: Path, **kwargs
    ) -> StepResult:
        """Reconstruction with Apple Object Capture."""
        logger.info("=" * 40)
        logger.info("STEP 2: Reconstruction (Apple Object Capture)")
        logger.info("=" * 40)

        from .reconstruction.apple_capture import AppleObjectCapture

        detail = kwargs.get("detail", "full")
        sensitivity = kwargs.get("sensitivity", "high")

        # Auto-detect ordering: drone (many sequential photos) → sequential
        # Phone (few sparse photos) → unordered
        if "ordering" in kwargs:
            ordering = kwargs["ordering"]
        else:
            n_photos = len(list(photo_dir.iterdir()))
            ordering = "sequential" if n_photos > 30 else "unordered"
            logger.info("Ordering auto-detect: %d photos → %s", n_photos, ordering)

        capture = AppleObjectCapture(
            detail=detail,
            ordering=ordering,
            sensitivity=sensitivity,
        )

        output_path = output_dir / "reconstruction.usdz"
        return capture.run(photo_dir, output_path)

    def _step_usdz_convert(self, usdz_path: Path, output_dir: Path) -> StepResult:
        """Convert USDZ to OBJ if needed."""
        logger.info("=" * 40)
        logger.info("STEP 2b: USDZ → OBJ conversion")
        logger.info("=" * 40)

        from .reconstruction.usdz_converter import usdz_to_obj

        obj_path = output_dir / "reconstruction.obj"
        success = usdz_to_obj(usdz_path, obj_path)

        if success:
            return StepResult(success=True, output_path=obj_path)
        return StepResult(success=False, errors=["USDZ → OBJ conversion failed"])

    def _step_cleaning(
        self, mesh_path: Path, output_dir: Path, **kwargs
    ) -> StepResult:
        """Step 3: Mesh cleaning.

        If the pipeline includes the rectangular base (skip_base=False),
        use minimal cleaning: no flat base, no close holes.
        The mesh must remain open for the rectangular base.
        """
        logger.info("=" * 40)
        logger.info("STEP 3: Mesh Cleaning")
        logger.info("=" * 40)

        from .cleaning.cleaner import MeshCleaner

        preset = kwargs.get("cleaning_preset", "standard")
        if preset == "gentle":
            config = CleaningConfig.gentle()
        elif preset == "aggressive":
            config = CleaningConfig.aggressive()
        else:
            config = CleaningConfig()

        # If the rectangular base will be added later, cleaning
        # must NOT close the mesh (no flat base, no close holes).
        # The mesh must remain open to find boundary loops.
        if not kwargs.get("skip_base", False):
            config.close_holes = False
            logger.info("Rectangular base active → minimal cleaning (no close holes, no flat base)")

        cleaner = MeshCleaner(config)
        cleaned_path = output_dir / "cleaned.obj"
        return cleaner.run(mesh_path, cleaned_path)

    def _step_remove_fragments(
        self, mesh_path: Path, output_dir: Path
    ) -> StepResult:
        """Step 3b: Remove disconnected components (residual fragments).

        Keeps only the main component (largest by face count).
        """
        try:
            import trimesh
            mesh = trimesh.load(str(mesh_path))
            components = mesh.split(only_watertight=False)

            if len(components) <= 1:
                return StepResult(success=True, output_path=mesh_path,
                                  metadata={"components": 1, "removed": 0})

            # Sort by face count, keep the largest
            components.sort(key=lambda c: len(c.faces), reverse=True)
            main = components[0]
            removed = len(components) - 1
            faces_removed = sum(len(c.faces) for c in components[1:])

            logger.info(
                "Fragments: %d components, removed %d (%d faces)",
                len(components), removed, faces_removed,
            )

            out_path = output_dir / "defragmented.stl"
            main.export(str(out_path))

            return StepResult(
                success=True,
                output_path=out_path,
                metadata={
                    "components_before": len(components),
                    "removed": removed,
                    "faces_removed": faces_removed,
                    "faces_kept": len(main.faces),
                },
            )
        except Exception as e:
            logger.warning("Fragment removal failed: %s", e)
            return StepResult(success=True, output_path=mesh_path)

    def _step_planar_flatten(
        self, mesh_path: Path, output_dir: Path, **kwargs
    ) -> StepResult:
        """Step 3c: Flatten planar regions (roofs, walls)."""
        logger.info("=" * 40)
        logger.info("STEP 3c: Planar Flattening")
        logger.info("=" * 40)

        try:
            import trimesh
            from .cleaning.base_builder import planar_flatten

            mesh = trimesh.load(str(mesh_path), force="mesh")
            faces_before = len(mesh.faces)

            mesh = planar_flatten(
                mesh,
                angle_threshold=kwargs.get("planar_angle_threshold", 15.0),
                min_region_faces=kwargs.get("planar_min_region", 50),
                strength=kwargs.get("planar_strength", 0.7),
            )

            out_path = output_dir / "planar.stl"
            mesh.export(str(out_path))

            return StepResult(
                success=True,
                output_path=out_path,
                metadata={"faces": len(mesh.faces)},
            )
        except Exception as e:
            logger.warning("Planar flatten failed: %s", e)
            return StepResult(success=True, output_path=mesh_path,
                              warnings=[f"Planar flatten failed: {e}"])

    def _step_decimate(
        self, mesh_path: Path, output_dir: Path, **kwargs
    ) -> StepResult:
        """Step 3d: Decimation quadric edge collapse."""
        logger.info("=" * 40)
        logger.info("STEP 3d: Decimation")
        logger.info("=" * 40)

        try:
            import trimesh
            from .cleaning.base_builder import decimate_mesh

            mesh = trimesh.load(str(mesh_path), force="mesh")
            faces_before = len(mesh.faces)
            target = kwargs.get("decimate_target", 1_000_000)

            mesh = decimate_mesh(mesh, target_faces=target)

            out_path = output_dir / "decimated.stl"
            mesh.export(str(out_path))

            return StepResult(
                success=True,
                output_path=out_path,
                metadata={
                    "faces_before": faces_before,
                    "faces_after": len(mesh.faces),
                    "target": target,
                },
            )
        except Exception as e:
            logger.warning("Decimation failed: %s", e)
            return StepResult(success=True, output_path=mesh_path,
                              warnings=[f"Decimation failed: {e}"])

    def _step_rectangular_base(
        self, mesh_path: Path, output_dir: Path, **kwargs
    ) -> StepResult:
        """Step 3c: Add closed rectangular base with smooth walls."""
        logger.info("=" * 40)
        logger.info("STEP 3c: Rectangular Base")
        logger.info("=" * 40)

        try:
            import trimesh
            import numpy as np
            from .cleaning.base_builder import add_rectangular_base, crop_mesh_to_rectangle

            mesh = trimesh.load(str(mesh_path), force="mesh")

            # Remove fragments first (keep only main component)
            components = mesh.split(only_watertight=False)
            if len(components) > 1:
                components.sort(key=lambda c: len(c.faces), reverse=True)
                mesh = components[0]

            mesh.fix_normals()
            faces_before = len(mesh.faces)

            base_mode = kwargs.get("base_mode", "wrap")
            margin = kwargs.get("base_margin", 2.0)
            base_height = kwargs.get("base_height", 5.0)

            if base_mode == "crop":
                logger.info("Base mode: crop (trim to inscribed rectangle)")
                mesh = crop_mesh_to_rectangle(
                    mesh,
                    margin=margin,
                    base_height=base_height,
                    auto_orient=False,
                )
            else:
                mesh = add_rectangular_base(
                    mesh,
                    auto_orient=False,
                    margin=margin,
                    base_height=base_height,
                )

            # Fix residual NM edges post-base
            if not mesh.is_watertight:
                from .cleaning.base_builder import _fix_non_manifold
                mesh = _fix_non_manifold(mesh)
                mesh.fix_normals()

            out_path = output_dir / "with_base.stl"
            mesh.export(str(out_path))

            # Re-check after STL export (float32 truncation can introduce NM edges)
            mesh_reloaded = trimesh.load(str(out_path), force="mesh")
            if not mesh_reloaded.is_watertight:
                logger.info("Post-export: STL float32 introduced %d NM edges, fixing...",
                            int((np.unique(np.sort(mesh_reloaded.edges, axis=1), axis=0, return_counts=True)[1] > 2).sum()))
                from .cleaning.base_builder import _fix_non_manifold
                mesh_reloaded = _fix_non_manifold(mesh_reloaded)
                mesh_reloaded.fix_normals()
                mesh_reloaded.export(str(out_path))
                mesh = mesh_reloaded

            logger.info(
                "Rectangular base: %d → %d faces, watertight=%s",
                faces_before, len(mesh.faces), mesh.is_watertight,
            )

            return StepResult(
                success=True,
                output_path=out_path,
                metadata={
                    "faces_before": faces_before,
                    "faces_after": len(mesh.faces),
                    "vertices": len(mesh.vertices),
                },
            )
        except Exception as e:
            logger.warning("Rectangular base failed: %s", e)
            return StepResult(success=True, output_path=mesh_path,
                              warnings=[f"Rectangular base failed: {e}"])

    def _step_scale(
        self, mesh_path: Path, output_dir: Path, target_mm: float
    ) -> StepResult:
        """Step 3f: Scale the mesh to target in mm."""
        logger.info("=" * 40)
        logger.info("STEP 3f: Scale to %.1f mm", target_mm)
        logger.info("=" * 40)

        try:
            import trimesh
            from .cleaning.base_builder import scale_mesh_to_mm

            mesh = trimesh.load(str(mesh_path), force="mesh")
            extents_before = mesh.extents.copy()

            mesh = scale_mesh_to_mm(mesh, target_mm)

            out_path = output_dir / "scaled.stl"
            mesh.export(str(out_path))

            return StepResult(
                success=True,
                output_path=out_path,
                metadata={
                    "target_mm": target_mm,
                    "extents_before": [round(float(e), 2) for e in extents_before],
                    "extents_after": [round(float(e), 2) for e in mesh.extents],
                },
            )
        except Exception as e:
            logger.warning("Scale failed: %s", e)
            return StepResult(success=True, output_path=mesh_path,
                              warnings=[f"Scale failed: {e}"])

    def _step_export(
        self, mesh_path: Path, output_dir: Path, prev_steps: dict
    ) -> StepResult:
        """Step 4: Export STL."""
        logger.info("=" * 40)
        logger.info("STEP 4: Export STL")
        logger.info("=" * 40)

        from .export.exporter import STLExporter

        exporter = STLExporter()
        stl_path = output_dir / "output.stl"

        # Collect metadata from previous steps
        metadata = {}
        for step_name, step_result in prev_steps.items():
            if step_result.metadata:
                metadata[step_name] = step_result.metadata

        return exporter.run(mesh_path, stl_path, metadata=metadata)

    def _save_report(
        self,
        result: PipelineResult,
        path: Path,
        input_path: Path | None = None,
        parameters: dict | None = None,
    ) -> None:
        """Save pipeline report as JSON."""
        data = {
            "success": result.success,
            "elapsed_seconds": round(result.elapsed_seconds, 1),
            "input_photos": str(input_path.resolve()) if input_path else None,
            "output": str(result.output_path) if result.output_path else None,
            "parameters": parameters or {},
            "cli_equivalent": self._build_cli_string(input_path, result.output_path, parameters),
            "errors": result.errors,
            "steps": {},
        }
        for name, step in result.steps.items():
            data["steps"][name] = {
                "success": step.success,
                "output": str(step.output_path) if step.output_path else None,
                "metadata": step.metadata,
                "errors": step.errors,
                "warnings": step.warnings,
            }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def _build_cli_string(
        input_path: Path | None,
        output_path: Path | None,
        parameters: dict | None,
    ) -> str:
        """Reconstruct the equivalent CLI command from parameters."""
        parts = ["snapsolid"]
        if input_path:
            parts.append(str(input_path))
        if output_path:
            parts.extend(["-o", str(output_path.parent)])

        if not parameters:
            return " ".join(parts)

        flag_map = {
            "detail": "--detail",
            "ordering": "--ordering",
            "sensitivity": "--sensitivity",
            "max_photos": "--max-photos",
            "cleaning_preset": "--cleaning-preset",
            "decimate_target": "--decimate-target",
            "planar_angle_threshold": "--planar-angle-threshold",
            "planar_min_region": "--planar-min-region",
            "planar_strength": "--planar-strength",
            "base_margin": "--base-margin",
            "base_height": "--base-height",
            "base_mode": "--base-mode",
            "scale_to_mm": "--scale-to-mm",
        }
        bool_flags = {
            "skip_quality_gate": "--skip-quality-gate",
            "skip_cleaning": "--skip-cleaning",
            "skip_base": "--skip-base",
            "planar_flatten": "--planar-flatten",
            "decimate": "--decimate",
        }

        for key, flag in flag_map.items():
            val = parameters.get(key)
            if val is not None:
                parts.extend([flag, str(val)])

        for key, flag in bool_flags.items():
            if parameters.get(key, False):
                parts.append(flag)

        return " ".join(parts)
