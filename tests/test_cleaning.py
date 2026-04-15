"""Tests for the mesh cleaning module."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import trimesh

from snapsolid.cleaning.analyzer import MeshAnalyzer
from snapsolid.cleaning.cleaner import MeshCleaner
from snapsolid.cleaning.validate import MeshValidator
from snapsolid.config import CleaningConfig

logging.basicConfig(level=logging.INFO)


def make_broken_cube() -> trimesh.Trimesh:
    """Create a cube with typical issues: missing face."""
    mesh = trimesh.creation.box(extents=[10, 10, 10])
    # Remove 2 triangles (= 1 cube face) to create a hole
    faces = mesh.faces[:-2]
    mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=faces, process=False)
    return mesh


def make_cube_with_noise() -> trimesh.Trimesh:
    """Cube with small disconnected components (noise)."""
    cube = trimesh.creation.box(extents=[10, 10, 10])
    noise = trimesh.creation.icosphere(radius=0.5)
    noise.apply_translation([20, 20, 20])
    combined = trimesh.util.concatenate([cube, noise])
    return combined


class TestAnalyzer:
    """Tests for MeshAnalyzer."""

    def test_analyze_good_cube(self):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        analyzer = MeshAnalyzer()
        report = analyzer.analyze(mesh)

        assert report.vertex_count > 0
        assert report.face_count > 0
        assert report.is_watertight
        assert report.is_printable
        assert len(report.issues) == 0

    def test_analyze_broken_cube(self):
        mesh = make_broken_cube()
        analyzer = MeshAnalyzer()
        report = analyzer.analyze(mesh)

        assert not report.is_watertight
        assert not report.is_printable
        assert len(report.issues) > 0

    def test_analyze_noisy_mesh(self):
        mesh = make_cube_with_noise()
        analyzer = MeshAnalyzer()
        report = analyzer.analyze(mesh)

        assert report.components_count > 1

    def test_summary_output(self):
        mesh = trimesh.creation.box(extents=[5, 5, 5])
        analyzer = MeshAnalyzer()
        report = analyzer.analyze(mesh)
        summary = report.summary()

        assert "Vertices:" in summary
        assert "Faces:" in summary
        assert "Watertight:" in summary


class TestValidator:
    """Tests for MeshValidator."""

    def test_good_cube_is_printable(self):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        validator = MeshValidator()
        result = validator.validate(mesh)

        assert result.is_printable
        assert all(result.checks.values())

    def test_broken_cube_not_printable(self):
        mesh = make_broken_cube()
        validator = MeshValidator()
        result = validator.validate(mesh)

        assert not result.is_printable


class TestCleaner:
    """Tests for MeshCleaner (full pipeline)."""

    def test_clean_broken_cube(self):
        mesh = make_broken_cube()
        config = CleaningConfig.standard()
        cleaner = MeshCleaner(config)

        result = cleaner.clean_mesh(mesh)

        assert not result.report_before.is_printable
        print("\n" + result.summary())

    def test_clean_noisy_mesh(self):
        mesh = make_cube_with_noise()
        config = CleaningConfig.standard()
        cleaner = MeshCleaner(config)

        result = cleaner.clean_mesh(mesh)

        assert result.report_after.components_count == 1
        print("\n" + result.summary())

    def test_clean_good_cube_noop(self):
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        config = CleaningConfig.standard()
        cleaner = MeshCleaner(config)

        result = cleaner.clean_mesh(mesh)

        assert result.report_before.is_printable
        assert result.validation.is_printable

    def test_clean_file_roundtrip(self):
        mesh = make_broken_cube()
        config = CleaningConfig.standard()
        cleaner = MeshCleaner(config)

        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "broken.stl"
            output_path = Path(tmp) / "cleaned.stl"

            mesh.export(str(input_path))
            result = cleaner.clean_file(input_path, output_path)

            assert output_path.exists()
            cleaned = trimesh.load(str(output_path), force="mesh")
            assert len(cleaned.faces) > 0

    def test_aggressive_config(self):
        mesh = trimesh.creation.icosphere(subdivisions=4)
        config = CleaningConfig.aggressive()
        config.target_faces = 500
        cleaner = MeshCleaner(config)

        result = cleaner.clean_mesh(mesh)

        assert result.report_after.face_count < result.report_before.face_count
        print(f"\nFaces: {result.report_before.face_count} -> {result.report_after.face_count}")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
