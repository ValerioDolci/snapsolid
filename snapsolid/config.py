"""Snapsolid configuration with sensible defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CleaningConfig:
    """Mesh cleaning parameters."""
    # Disconnected components
    remove_small_components: bool = True
    min_component_ratio: float = 0.1  # keep components > 10% of total

    # Topology
    fix_non_manifold: bool = True
    remove_degenerate_faces: bool = True
    remove_duplicate_faces: bool = True

    # Holes
    close_holes: bool = True
    max_hole_size: int = 100  # max edges per hole to close

    # Normals
    fix_normals: bool = True

    # Self-intersections
    fix_self_intersections: bool = True

    # Simplification
    simplify: bool = False
    target_faces: int = 100_000
    smooth: bool = True
    smooth_iterations: int = 3
    smooth_method: str = "adaptive"  # "adaptive", "taubin" or "laplacian"

    # Post-processing mesh (drone)
    planar_flatten: bool = False       # region growing + projection onto mean plane
    planar_angle_threshold: float = 15.0  # angle threshold for region growing (degrees)
    planar_min_region: int = 50        # minimum faces per planar region
    planar_strength: float = 0.7       # projection strength (0-1)
    decimate: bool = False             # decimation quadric edge collapse
    decimate_target: int = 1_000_000   # target faces after decimation

    # Preconfigured presets
    @classmethod
    def gentle(cls) -> "CleaningConfig":
        """Gentle cleaning — minimal changes."""
        return cls(
            simplify=False,
            smooth=False,
            max_hole_size=30,
        )

    @classmethod
    def standard(cls) -> "CleaningConfig":
        """Standard cleaning — good compromise."""
        return cls()

    @classmethod
    def aggressive(cls) -> "CleaningConfig":
        """Aggressive cleaning — printability priority."""
        return cls(
            max_hole_size=300,
            simplify=True,
            target_faces=50_000,
            smooth=True,
            smooth_iterations=5,
        )


@dataclass
class PipelineConfig:
    """Global pipeline configuration."""
    # Active modules
    ingest: str = "photo"
    gap_filling: str = "none"
    reconstruction: str = "apple"
    cleaning: str = "auto"
    export: str = "stl"

    # Cleaning parameters
    cleaning_config: CleaningConfig = field(default_factory=CleaningConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> "PipelineConfig":
        """Load config from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        config = cls()
        if "pipeline" in data:
            for key in ("ingest", "gap_filling", "reconstruction", "cleaning", "export"):
                if key in data["pipeline"]:
                    setattr(config, key, data["pipeline"][key])

        if "cleaning" in data:
            cleaning_data = data["cleaning"]
            # Support presets
            preset = cleaning_data.pop("preset", None)
            if preset == "gentle":
                config.cleaning_config = CleaningConfig.gentle()
            elif preset == "aggressive":
                config.cleaning_config = CleaningConfig.aggressive()
            else:
                config.cleaning_config = CleaningConfig()
            # Override individual parameters
            for key, value in cleaning_data.items():
                if hasattr(config.cleaning_config, key):
                    setattr(config.cleaning_config, key, value)

        return config

    def to_yaml(self, path: Path) -> None:
        """Save config to YAML file."""
        data = {
            "pipeline": {
                "ingest": self.ingest,
                "gap_filling": self.gap_filling,
                "reconstruction": self.reconstruction,
                "cleaning": self.cleaning,
                "export": self.export,
            },
            "cleaning": {
                k: v for k, v in self.cleaning_config.__dict__.items()
            },
        }
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
