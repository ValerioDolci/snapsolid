"""USDZ/USDC → OBJ converter.

Apple Object Capture produces USDZ (which contains binary USDC).
Neither trimesh nor pymeshlab read USD, so:
1. Extract USDC from USDZ (it's a zip)
2. Use usdcat (macOS built-in) to convert USDC → USDA (text)
3. Parse USDA text to extract vertices and faces
4. Save as OBJ
"""

from __future__ import annotations

import logging
import re
import subprocess
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

logger = logging.getLogger(__name__)


def usdz_to_obj(usdz_path: Path, obj_path: Path) -> bool:
    """Convert a USDZ file to OBJ.

    Returns True if conversion succeeds.
    """
    usdz_path = Path(usdz_path)
    obj_path = Path(obj_path)

    with TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Step 1: Extract USDC from USDZ
        usdc_path = _extract_usdc(usdz_path, tmpdir)
        if usdc_path is None:
            return False

        # Step 2: Convert USDC → USDA (text) via usdcat
        usda_path = tmpdir / "mesh.usda"
        if not _usdc_to_usda(usdc_path, usda_path):
            return False

        # Step 3: Parse USDA and extract vertices/faces
        vertices, faces = _parse_usda(usda_path)
        if vertices is None or faces is None:
            return False

        # Step 4: Save as OBJ
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        _write_obj(vertices, faces, obj_path)

        logger.info("Converted %s → %s (%d vertices, %d faces)",
                     usdz_path.name, obj_path.name, len(vertices), len(faces))
        return True


def _extract_usdc(usdz_path: Path, output_dir: Path) -> Path | None:
    """Extract the USDC file from USDZ (which is a zip)."""
    try:
        with zipfile.ZipFile(str(usdz_path), "r") as zf:
            usdc_files = [f for f in zf.namelist() if f.endswith(".usdc")]
            if not usdc_files:
                logger.error("No USDC file found in USDZ")
                return None
            # Take the first USDC
            zf.extract(usdc_files[0], str(output_dir))
            return output_dir / usdc_files[0]
    except Exception as e:
        logger.error("USDZ extraction error: %s", e)
        return None


def _usdc_to_usda(usdc_path: Path, usda_path: Path) -> bool:
    """Convert USDC (binary) to USDA (text) using usdcat."""
    try:
        result = subprocess.run(
            ["usdcat", str(usdc_path), "-o", str(usda_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("usdcat failed: %s", result.stderr)
            return False
        return usda_path.exists()
    except FileNotFoundError:
        logger.error("usdcat not found. Requires Xcode Command Line Tools.")
        return False
    except Exception as e:
        logger.error("usdcat error: %s", e)
        return False


def _parse_usda(usda_path: Path) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Parse a USDA file and extract vertices and faces."""
    text = usda_path.read_text()

    # Extract vertices: point3f[] points = [(...), (...), ...]
    vertices = _extract_float_array(text, r"point3f\[\]\s+points\s*=\s*\[([^\]]+)\]")
    if vertices is None:
        logger.error("Vertices not found in USDA file")
        return None, None

    # Extract face indices: int[] faceVertexIndices = [...]
    face_indices = _extract_int_array(text, r"int\[\]\s+faceVertexIndices\s*=\s*\[([^\]]+)\]")
    if face_indices is None:
        logger.error("Face indices not found in USDA file")
        return None, None

    # Extract face vertex counts (how many vertices per face)
    face_counts = _extract_int_array(text, r"int\[\]\s+faceVertexCounts\s*=\s*\[([^\]]+)\]")

    # Build faces
    if face_counts is not None:
        faces = []
        idx = 0
        for count in face_counts:
            if count == 3:
                faces.append(face_indices[idx:idx + 3])
            elif count == 4:
                # Triangulate quads
                q = face_indices[idx:idx + 4]
                faces.append([q[0], q[1], q[2]])
                faces.append([q[0], q[2], q[3]])
            idx += count
        faces = np.array(faces)
    else:
        # Assume triangles
        faces = face_indices.reshape(-1, 3)

    logger.info("Parsed USDA: %d vertices, %d faces", len(vertices), len(faces))
    return vertices, faces


def _extract_float_array(text: str, pattern: str) -> np.ndarray | None:
    """Extract array of float3 tuples from USDA text."""
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    content = match.group(1)
    # Find all tuples (x, y, z)
    tuples = re.findall(r"\(\s*([-\d.e+]+)\s*,\s*([-\d.e+]+)\s*,\s*([-\d.e+]+)\s*\)", content)
    if not tuples:
        return None
    return np.array(tuples, dtype=np.float64)


def _extract_int_array(text: str, pattern: str) -> np.ndarray | None:
    """Extract integer array from USDA text."""
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    content = match.group(1)
    numbers = re.findall(r"[-\d]+", content)
    if not numbers:
        return None
    return np.array(numbers, dtype=np.int64)


def _write_obj(vertices: np.ndarray, faces: np.ndarray, path: Path) -> None:
    """Write mesh as OBJ file."""
    with open(path, "w") as f:
        f.write(f"# Snapsolid USDZ->OBJ converter\n")
        f.write(f"# {len(vertices)} vertices, {len(faces)} faces\n")
        for v in vertices:
            f.write(f"v {v[0]} {v[1]} {v[2]}\n")
        for face in faces:
            # OBJ uses 1-based indices
            f.write(f"f {face[0]+1} {face[1]+1} {face[2]+1}\n")
