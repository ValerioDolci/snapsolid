"""CLI entry point for Snapsolid."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import PipelineConfig
from .pipeline import Pipeline


def main():
    """Entry point: snapsolid <input_dir> -o <output_dir> [options]."""
    parser = argparse.ArgumentParser(
        prog="snapsolid",
        description="Photogrammetry to 3D-printable STL pipeline",
    )
    parser.add_argument(
        "input", type=Path,
        help="Directory with input photos (JPEG/PNG/HEIC)",
    )
    parser.add_argument(
        "-o", "--output", type=Path, required=True,
        help="Output directory for STL and reports",
    )
    parser.add_argument(
        "--detail", default="full",
        choices=["reduced", "medium", "full", "raw"],
        help="Reconstruction detail level (default: full)",
    )
    parser.add_argument(
        "--ordering", default=None,
        choices=["sequential", "unordered"],
        help="Photo ordering (default: auto-detect)",
    )
    parser.add_argument(
        "--sensitivity", default="high",
        choices=["normal", "high"],
        help="Object Capture sensitivity (default: high)",
    )
    parser.add_argument(
        "--max-photos", type=int, default=0,
        help="Max photos for quality gate (0 = use all)",
    )
    parser.add_argument(
        "--cleaning-preset", default="standard",
        choices=["gentle", "standard", "aggressive"],
        help="Mesh cleaning preset (default: standard)",
    )
    parser.add_argument(
        "--planar-flatten", action="store_true",
        help="Enable planar flattening (for buildings)",
    )
    parser.add_argument(
        "--planar-angle-threshold", type=float, default=15.0,
        help="Angle threshold for planar region growing in degrees (default: 15.0)",
    )
    parser.add_argument(
        "--planar-min-region", type=int, default=50,
        help="Minimum faces for a planar region to be flattened (default: 50)",
    )
    parser.add_argument(
        "--planar-strength", type=float, default=0.7,
        help="Projection strength 0-1, 1.0 = fully flat (default: 0.7)",
    )
    parser.add_argument(
        "--decimate", action="store_true",
        help="Enable mesh decimation",
    )
    parser.add_argument(
        "--decimate-target", type=int, default=1_000_000,
        help="Target face count for decimation (default: 1M)",
    )
    parser.add_argument(
        "--skip-quality-gate", action="store_true",
        help="Skip photo quality gate",
    )
    parser.add_argument(
        "--skip-cleaning", action="store_true",
        help="Skip mesh cleaning",
    )
    parser.add_argument(
        "--skip-base", action="store_true",
        help="Skip rectangular base",
    )
    parser.add_argument(
        "--base-mode", default="wrap",
        choices=["wrap", "crop"],
        help="Base mode: wrap (external) or crop (trim mesh to rectangle) (default: wrap)",
    )
    parser.add_argument(
        "--base-margin", type=float, default=2.0,
        help="Base margin around mesh in mesh units (default: 2.0)",
    )
    parser.add_argument(
        "--base-height", type=float, default=5.0,
        help="Base wall height in mesh units (default: 5.0)",
    )
    parser.add_argument(
        "--scale-to-mm", type=float, default=0,
        help="Scale model so longest side = N mm (0 = no scaling)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Validate input
    if not args.input.is_dir():
        print(f"Error: {args.input} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Build kwargs
    kwargs = {
        "detail": args.detail,
        "sensitivity": args.sensitivity,
        "max_photos": args.max_photos,
        "cleaning_preset": args.cleaning_preset,
        "planar_flatten": args.planar_flatten,
        "planar_angle_threshold": args.planar_angle_threshold,
        "planar_min_region": args.planar_min_region,
        "planar_strength": args.planar_strength,
        "decimate": args.decimate,
        "decimate_target": args.decimate_target,
        "skip_quality_gate": args.skip_quality_gate,
        "skip_cleaning": args.skip_cleaning,
        "skip_base": args.skip_base,
        "base_mode": args.base_mode,
        "base_margin": args.base_margin,
        "base_height": args.base_height,
        "scale_to_mm": args.scale_to_mm,
    }
    if args.ordering:
        kwargs["ordering"] = args.ordering

    # Run pipeline
    pipeline = Pipeline(PipelineConfig())
    result = pipeline.run(args.input, args.output, **kwargs)

    print(result.summary())

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
