"""Wrapper Python per Apple Object Capture CLI."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..core.base import ReconstructionStep, StepResult

logger = logging.getLogger(__name__)

# Path to compiled binary (relative to project root)
DEFAULT_CLI_PATH = Path(__file__).parent.parent.parent / "tools" / "photogrammetry-cli" / ".build" / "release" / "photogrammetry-cli"


class AppleObjectCapture(ReconstructionStep):
    """3D reconstruction using Apple Object Capture (RealityKit).

    Requires macOS 13+ and Apple Silicon.
    Input: directory of JPEG/HEIC/PNG photos
    Output: OBJ file (mesh)
    """

    def __init__(
        self,
        cli_path: Path | None = None,
        detail: str = "medium",
        ordering: str = "unordered",
        sensitivity: str = "normal",
    ):
        self.cli_path = cli_path or DEFAULT_CLI_PATH
        self.detail = detail
        self.ordering = ordering
        self.sensitivity = sensitivity

    @property
    def name(self) -> str:
        return "apple_object_capture"

    def validate_input(self, input_path: Path) -> bool:
        """Verify that the directory contains photos."""
        if not input_path.is_dir():
            return False
        extensions = {".jpg", ".jpeg", ".heic", ".png"}
        photos = [f for f in input_path.iterdir() if f.suffix.lower() in extensions]
        return len(photos) >= 3  # minimum 3 photos

    def run(self, input_path: Path, output_path: Path, **kwargs) -> StepResult:
        """Run reconstruction from photos to OBJ mesh."""
        # Override parameters from kwargs
        detail = kwargs.get("detail", self.detail)
        ordering = kwargs.get("ordering", self.ordering)
        sensitivity = kwargs.get("sensitivity", self.sensitivity)

        # Verify binary
        if not self.cli_path.exists():
            return StepResult(
                success=False,
                errors=[f"CLI not found: {self.cli_path}. Build with: cd tools/photogrammetry-cli && swift build -c release"],
            )

        # Verify input
        if not self.validate_input(input_path):
            extensions = {".jpg", ".jpeg", ".heic", ".png"}
            photos = list(f for f in input_path.iterdir() if f.suffix.lower() in extensions) if input_path.is_dir() else []
            return StepResult(
                success=False,
                errors=[f"At least 3 photos required in directory. Found: {len(photos)}"],
            )

        # Object Capture works best with USDZ as output
        if output_path.suffix.lower() not in (".obj", ".usdz"):
            output_path = output_path.with_suffix(".usdz")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing output to avoid "invalidOutput" from Object Capture
        if output_path.exists():
            logger.info("Removing existing output file: %s", output_path)
            output_path.unlink()

        # Count photos
        extensions = {".jpg", ".jpeg", ".heic", ".png"}
        photos = [f for f in input_path.iterdir() if f.suffix.lower() in extensions]
        logger.info("Reconstruction from %d photos, detail=%s", len(photos), detail)

        # Launch CLI — Object Capture requires absolute paths
        cmd = [
            str(self.cli_path.resolve()),
            str(input_path.resolve()),
            str(output_path.resolve()),
            "--detail", detail,
            "--ordering", ordering,
            "--sensitivity", sensitivity,
        ]

        logger.info("Command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 60 minutes max
            )

            if result.returncode != 0:
                logger.error("CLI stderr: %s", result.stderr)
                logger.error("CLI stdout: %s", result.stdout)
                return StepResult(
                    success=False,
                    errors=[f"CLI failed (exit {result.returncode}): {result.stderr or result.stdout}"],
                )

            if not output_path.exists():
                return StepResult(
                    success=False,
                    errors=["CLI completed but output file not found"],
                )

            logger.info("Reconstruction complete: %s", output_path)
            return StepResult(
                success=True,
                output_path=output_path,
                metadata={
                    "photos": len(photos),
                    "detail": detail,
                    "output_size_mb": output_path.stat().st_size / (1024 * 1024),
                },
            )

        except subprocess.TimeoutExpired:
            return StepResult(
                success=False,
                errors=["Timeout: reconstruction too slow (>10 min)"],
            )
        except Exception as e:
            return StepResult(
                success=False,
                errors=[f"Error: {e}"],
            )
