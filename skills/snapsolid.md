---
allowed-tools: Bash, Read, Glob, Grep, Agent
description: Run the Snapsolid pipeline — photos to printable STL
---

## Context

- User input: $ARGUMENTS

## Your task

Run the Snapsolid photogrammetry-to-STL pipeline. The user provides a photo directory and optional parameters.

### 1. Parse arguments

The user provides arguments in free form. Extract:
- **Photo directory** (required) — the path to the folder with photos
- **Output directory** (optional, default: `output/` inside the project)
- **Parameters** (optional): `--detail`, `--base-mode`, `--scale-to-mm`, `--planar-flatten`, `--decimate`, `--decimate-target`, `--cleaning-preset`, `--skip-quality-gate`, etc.

If no photo directory is provided, ask the user.

### 2. Pre-flight checks

Before running:
- Verify the photo directory exists and contains image files (JPG/PNG/HEIC)
- Count the photos and report: "Found N photos in /path/to/dir"
- Check the Swift CLI binary exists at `tools/photogrammetry-cli/.build/release/photogrammetry-cli` relative to the project root
- Estimate time: ~1 min per 10 photos for `full`, ~2 min per 10 photos for `raw`

### 3. Run the pipeline

Use the project's Python environment to run:

```python
from snapsolid.pipeline import Pipeline
from snapsolid.config import PipelineConfig

pipeline = Pipeline(PipelineConfig())
result = pipeline.run(
    input_path="PHOTO_DIR",
    output_dir="OUTPUT_DIR",
    # ... user-specified parameters
)
print(result.summary())
```

Run in background if the reconstruction is expected to take more than 2 minutes. Monitor progress by tailing the log.

### 4. Post-flight checks

After the pipeline completes:
- Load the output STL and verify: watertight, face count, extents, volume
- Report results clearly to the user
- If watertight=False, explain what went wrong and suggest fixes
- Show the path to the output STL and the pipeline_report.json

### Important

- Always show what you're about to do before running
- For raw detail with >100 photos, warn that it will take 15-30 min
- If the pipeline fails, read the log and diagnose the issue
