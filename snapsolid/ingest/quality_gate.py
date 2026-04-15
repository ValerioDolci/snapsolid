"""Quality gate for input photos — filter before sending to Object Capture.

Checks:
1. Blur detection (Laplacian variance)
2. Exposure (brightness + contrast)
3. Overlap between consecutive photos (ORB feature matching)
4. Intelligent subset selection (minimum baseline between frames)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


@dataclass
class PhotoQuality:
    """Quality metrics for a single photo."""
    file: str
    blur: float
    brightness: float
    contrast: float
    n_features: int
    passed: bool = True
    reject_reasons: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    """Aggregated quality gate report."""
    total: int = 0
    passed: int = 0
    rejected: int = 0
    photos: list[PhotoQuality] = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Quality Gate: {self.passed}/{self.total} photos OK",
            f"  Rejected: {self.rejected}",
        ]
        if self.rejected > 0:
            reasons = {}
            for p in self.photos:
                for r in p.reject_reasons:
                    reasons[r] = reasons.get(r, 0) + 1
            for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
                lines.append(f"    {reason}: {count}")
        return "\n".join(lines)

    @property
    def good_photos(self) -> list[PhotoQuality]:
        return [p for p in self.photos if p.passed]


@dataclass
class OverlapPair:
    """Overlap between two photos."""
    file_a: str
    file_b: str
    n_matches: int
    good_matches: int


@dataclass
class SubsetResult:
    """Result of subset selection."""
    files: list[str]
    overlaps: list[OverlapPair]
    mean_overlap: float
    min_overlap: int


def _read_gps(photo_path: Path) -> tuple[float, float, float] | None:
    """Read GPS coordinates from EXIF. Returns (lat, lon, alt) or None."""
    try:
        from PIL import Image
        img = Image.open(str(photo_path))
        exif = img._getexif()
        if not exif:
            return None
        gps = exif.get(34853)
        if not gps or 2 not in gps or 4 not in gps:
            return None

        def dms_to_dd(dms, ref):
            d, m, s = float(dms[0]), float(dms[1]), float(dms[2])
            dd = d + m / 60 + s / 3600
            if ref in ("S", "W"):
                dd = -dd
            return dd

        lat = dms_to_dd(gps[2], gps[1])
        lon = dms_to_dd(gps[4], gps[3])
        alt = float(gps[6]) if 6 in gps else 0.0
        return (lat, lon, alt)
    except Exception:
        return None


class QualityGate:
    """Analyze and filter photos before reconstruction.

    Usage:
        gate = QualityGate()
        report = gate.analyze(photo_dir)
        subset = gate.select_subset(photo_dir, report, max_photos=80)
    """

    def __init__(
        self,
        blur_threshold: float | None = None,
        blur_percentile: float = 10.0,
        brightness_min: float = 40.0,
        brightness_max: float = 220.0,
        min_features: int = 100,
        analysis_resolution: int = 2000,
    ):
        self.blur_threshold = blur_threshold
        self.blur_percentile = blur_percentile
        self.brightness_min = brightness_min
        self.brightness_max = brightness_max
        self.min_features = min_features
        self.analysis_resolution = analysis_resolution

    def analyze(self, photo_dir: Path) -> QualityReport:
        """Analyze all photos in a directory.

        Returns:
            QualityReport with metrics for each photo.
        """
        photo_dir = Path(photo_dir)
        files = sorted(
            f.name for f in photo_dir.iterdir()
            if f.suffix.lower() in PHOTO_EXTENSIONS
        )
        logger.info("Quality gate: analyzing %d photos in %s", len(files), photo_dir)

        # Phase 1: compute metrics for each photo
        photos = []
        orb = cv2.ORB_create(1000)

        for i, fname in enumerate(files):
            path = str(photo_dir / fname)
            img = cv2.imread(path)
            if img is None:
                logger.warning("Unable to read %s, skip", fname)
                continue

            # Resize for analysis
            h, w = img.shape[:2]
            scale = self.analysis_resolution / max(h, w)
            if scale < 1.0:
                small = cv2.resize(img, None, fx=scale, fy=scale)
            else:
                small = img
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            blur = cv2.Laplacian(gray, cv2.CV_64F).var()
            brightness = float(gray.mean())
            contrast = float(gray.std())
            kps, _ = orb.detectAndCompute(gray, None)
            n_features = len(kps) if kps is not None else 0

            photos.append(PhotoQuality(
                file=fname,
                blur=round(blur, 1),
                brightness=round(brightness, 1),
                contrast=round(contrast, 1),
                n_features=n_features,
            ))

            if (i + 1) % 50 == 0:
                logger.info("  %d/%d analyzed", i + 1, len(files))

        # Phase 2: determine blur threshold
        if self.blur_threshold is not None:
            blur_thresh = self.blur_threshold
        else:
            blurs = [p.blur for p in photos]
            blur_thresh = float(np.percentile(blurs, self.blur_percentile))

        # Phase 3: apply filters
        for p in photos:
            if p.blur < blur_thresh:
                p.passed = False
                p.reject_reasons.append("blur")
            if p.brightness < self.brightness_min:
                p.passed = False
                p.reject_reasons.append("underexposed")
            if p.brightness > self.brightness_max:
                p.passed = False
                p.reject_reasons.append("overexposed")
            if p.n_features < self.min_features:
                p.passed = False
                p.reject_reasons.append("low_features")

        report = QualityReport(
            total=len(photos),
            passed=sum(1 for p in photos if p.passed),
            rejected=sum(1 for p in photos if not p.passed),
            photos=photos,
            thresholds={
                "blur": blur_thresh,
                "brightness_min": self.brightness_min,
                "brightness_max": self.brightness_max,
                "min_features": self.min_features,
            },
        )

        self._last_photos = photos
        logger.info(report.summary())
        return report

    def compute_overlaps(
        self,
        photo_dir: Path,
        files: list[str],
    ) -> list[OverlapPair]:
        """Compute overlap (ORB matching) between consecutive photos.

        Args:
            photo_dir: photo directory
            files: sorted list of file names

        Returns:
            List of OverlapPair between consecutive photos.
        """
        photo_dir = Path(photo_dir)
        orb = cv2.ORB_create(2000)
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)

        overlaps = []
        prev_des = None
        prev_name = None

        for fname in files:
            path = str(photo_dir / fname)
            img = cv2.imread(path)
            if img is None:
                continue

            h, w = img.shape[:2]
            scale = 1500 / max(h, w)
            small = cv2.resize(img, None, fx=scale, fy=scale)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            _, des = orb.detectAndCompute(gray, None)

            if prev_des is not None and des is not None:
                matches = bf.match(prev_des, des)
                good = [m for m in matches if m.distance < 50]
                overlaps.append(OverlapPair(
                    file_a=prev_name,
                    file_b=fname,
                    n_matches=len(matches),
                    good_matches=len(good),
                ))

            prev_des = des
            prev_name = fname

        return overlaps

    def select_subset(
        self,
        photo_dir: Path,
        report: QualityReport,
        max_photos: int = 0,
        min_overlap: int = 20,
    ) -> SubsetResult:
        """Select a subset of photos with good overlap.

        If max_photos=0 or good photos <= max_photos, uses all of them.
        Otherwise tries GPS spatial selection; if GPS unavailable
        uses contiguous window with best overlap.

        Args:
            photo_dir: photo directory
            report: result from analyze()
            max_photos: maximum number of photos (0 = use all)
            min_overlap: minimum overlap (good matches) between consecutive photos

        Returns:
            SubsetResult with list of selected files.
        """
        good_files = [p.file for p in report.good_photos]

        if max_photos <= 0 or len(good_files) <= max_photos:
            logger.info("Only %d good photos, using all of them", len(good_files))
            selected_files = good_files
        else:
            # Try GPS spatial selection
            selected_files = self._select_spatial(
                photo_dir, good_files, max_photos
            )
            if selected_files is None:
                # Fallback: contiguous window
                selected_files = self._select_contiguous(
                    photo_dir, good_files, max_photos, min_overlap
                )

        # Compute overlap of selected subset
        overlaps = self.compute_overlaps(photo_dir, selected_files)
        ov_vals = [o.good_matches for o in overlaps]

        return SubsetResult(
            files=selected_files,
            overlaps=overlaps,
            mean_overlap=float(np.mean(ov_vals)) if ov_vals else 0,
            min_overlap=min(ov_vals) if ov_vals else 0,
        )

    def _select_spatial(
        self,
        photo_dir: Path,
        good_files: list[str],
        max_photos: int,
    ) -> list[str] | None:
        """Selection based on GPS spatial coverage.

        Divides the area into a grid and picks the best photos per cell,
        ensuring uniform coverage. If GPS unavailable, returns None.
        """
        photo_dir = Path(photo_dir)

        # Read GPS for all photos
        gps_data = {}
        for fname in good_files:
            coords = _read_gps(photo_dir / fname)
            if coords is not None:
                gps_data[fname] = coords

        # Need GPS for at least 50% of photos
        if len(gps_data) < len(good_files) * 0.5:
            logger.info(
                "GPS available only for %d/%d photos, using contiguous selection",
                len(gps_data), len(good_files),
            )
            return None

        logger.info(
            "Spatial selection: %d photos with GPS out of %d",
            len(gps_data), len(good_files),
        )

        # Convert to local metric coordinates (approximation)
        files_with_gps = list(gps_data.keys())
        lats = np.array([gps_data[f][0] for f in files_with_gps])
        lons = np.array([gps_data[f][1] for f in files_with_gps])

        # Degrees → meters (local approximation)
        lat_center = lats.mean()
        m_per_lat = 111320.0
        m_per_lon = 111320.0 * np.cos(np.radians(lat_center))

        x = (lons - lons.mean()) * m_per_lon  # east-west in meters
        y = (lats - lats.mean()) * m_per_lat  # north-south in meters

        # Find quality (blur score) for each photo — higher = sharper
        blur_scores = {}
        for fname in files_with_gps:
            # Search in the PhotoQuality list
            for p in self._last_photos if hasattr(self, "_last_photos") else []:
                if p.file == fname:
                    blur_scores[fname] = p.blur
                    break
            else:
                blur_scores[fname] = 1.0  # fallback

        # Strategy: adaptive grid + farthest point sampling
        # 1. Start from the most central photo
        # 2. Iteratively add the photo farthest from the selected set
        # This ensures maximum spatial coverage
        selected_idx = []
        coords = np.column_stack([x, y])

        # Start from center
        center = np.array([0.0, 0.0])
        dists = np.linalg.norm(coords - center, axis=1)
        first = np.argmin(dists)
        selected_idx.append(first)

        # Farthest point sampling
        min_dists = np.full(len(coords), np.inf)
        for _ in range(max_photos - 1):
            # Update minimum distance from selected set
            last = coords[selected_idx[-1]]
            dists = np.linalg.norm(coords - last, axis=1)
            min_dists = np.minimum(min_dists, dists)

            # Exclude already selected
            min_dists_masked = min_dists.copy()
            for idx in selected_idx:
                min_dists_masked[idx] = -1

            # Pick the farthest
            next_idx = np.argmax(min_dists_masked)
            selected_idx.append(next_idx)

        selected_files = sorted(
            [files_with_gps[i] for i in selected_idx]
        )

        # Log coverage
        sel_x = x[selected_idx]
        sel_y = y[selected_idx]
        logger.info(
            "Spatial selection: %d photos, coverage %.0fx%.0f m",
            len(selected_files),
            sel_x.max() - sel_x.min(),
            sel_y.max() - sel_y.min(),
        )

        return selected_files

    def _select_contiguous(
        self,
        photo_dir: Path,
        good_files: list[str],
        max_photos: int,
        min_overlap: int,
    ) -> list[str]:
        """Selection based on contiguous window with best overlap."""
        logger.info("Computing overlap between %d good photos...", len(good_files))
        overlaps = self.compute_overlaps(photo_dir, good_files)
        overlap_vals = [o.good_matches for o in overlaps]

        best_score = -1
        best_start = 0
        window = max_photos - 1

        for start in range(len(overlap_vals) - window + 1):
            chunk = overlap_vals[start:start + window]
            min_ov = min(chunk)
            if min_ov < min_overlap:
                continue
            score = np.mean(chunk) * (min_ov + 1)
            if score > best_score:
                best_score = score
                best_start = start

        selected_files = good_files[best_start:best_start + max_photos]
        if not selected_files:
            logger.warning("No valid contiguous window found, returning all %d photos", len(good_files))
            return good_files
        logger.info(
            "Selected %d photos (idx %d-%d): %s → %s",
            len(selected_files), best_start, best_start + max_photos,
            selected_files[0], selected_files[-1],
        )
        return selected_files
