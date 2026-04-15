"""Tests of the cleaner on meshes with realistic issues."""

import logging
import time
from pathlib import Path

import trimesh

from snapsolid.cleaning.cleaner import MeshCleaner
from snapsolid.config import CleaningConfig

logging.basicConfig(level=logging.WARNING)  # less verbose for the report

SAMPLES = Path(__file__).parent.parent / "samples"
OUTPUT = Path(__file__).parent.parent / "samples" / "cleaned"


def run_benchmark():
    """Run the cleaner on all samples and produce a report."""
    OUTPUT.mkdir(exist_ok=True)
    config = CleaningConfig.standard()
    cleaner = MeshCleaner(config)

    stl_files = sorted(SAMPLES.glob("*.stl"))
    if not stl_files:
        print("No STL files in samples/. Generate samples first.")
        return

    results = []
    print("=" * 70)
    print("SNAPSOLID — Benchmark on realistic meshes")
    print("=" * 70)

    for stl_file in stl_files:
        print(f"\n{'─' * 70}")
        print(f"File: {stl_file.name}")
        print(f"{'─' * 70}")

        output_file = OUTPUT / f"cleaned_{stl_file.name}"

        t0 = time.time()
        try:
            result = cleaner.clean_file(stl_file, output_file)
            elapsed = time.time() - t0

            rb = result.report_before
            ra = result.report_after
            v = result.validation

            row = {
                "file": stl_file.name,
                "faces_before": rb.face_count,
                "faces_after": ra.face_count,
                "issues_before": len(rb.issues),
                "issues_after": len(ra.issues),
                "watertight_before": rb.is_watertight,
                "watertight_after": ra.is_watertight,
                "printable": v.is_printable,
                "time_s": elapsed,
            }
            results.append(row)

            print(f"  Faces:      {rb.face_count:>6} -> {ra.face_count:>6}")
            print(f"  Issues:     {len(rb.issues):>6} -> {len(ra.issues):>6}")
            print(f"  Watertight: {'YES' if rb.is_watertight else 'NO':>6} -> {'YES' if ra.is_watertight else 'NO':>6}")
            print(f"  Printable:  {'YES' if v.is_printable else 'NO':>6}")
            print(f"  Time:       {elapsed:.2f}s")

            if not v.is_printable:
                failed = [k for k, v in v.checks.items() if not v]
                print(f"  Failed checks: {', '.join(failed)}")

            if v.warnings:
                for w in v.warnings:
                    print(f"  WARN: {w}")

        except Exception as e:
            elapsed = time.time() - t0
            print(f"  ERROR: {e}")
            results.append({
                "file": stl_file.name,
                "printable": False,
                "time_s": elapsed,
                "error": str(e),
            })

    # Final summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    total = len(results)
    ok = sum(1 for r in results if r.get("printable", False))
    print(f"\n  Total:      {total} meshes")
    print(f"  Printable:  {ok}/{total} ({100*ok/total:.0f}%)")
    print(f"  Failed:     {total - ok}/{total}")
    total_time = sum(r.get("time_s", 0) for r in results)
    print(f"  Total time: {total_time:.2f}s")

    print(f"\n{'─' * 70}")
    print(f"  {'File':<30} {'Before':>8} {'After':>8} {'WT':>4} {'OK':>4} {'Time':>6}")
    print(f"  {'─'*30} {'─'*8} {'─'*8} {'─'*4} {'─'*4} {'─'*6}")
    for r in results:
        if "error" in r:
            print(f"  {r['file']:<30} {'ERROR':>8}")
            continue
        print(
            f"  {r['file']:<30} "
            f"{r['faces_before']:>8} "
            f"{r['faces_after']:>8} "
            f"{'YES' if r['watertight_after'] else 'NO':>4} "
            f"{'YES' if r['printable'] else 'NO':>4} "
            f"{r['time_s']:>5.2f}s"
        )


if __name__ == "__main__":
    run_benchmark()
