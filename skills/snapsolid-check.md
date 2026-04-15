---
allowed-tools: Bash, Read
description: Analyze an STL file — check printability, topology, and dimensions
---

## Context

- User input: $ARGUMENTS

## Your task

Analyze an STL (or OBJ/PLY) mesh file and report its printability status.

### 1. Parse arguments

The user provides a file path. If not provided, ask for it.

### 2. Run analysis

Load the mesh and compute:
- **Geometry**: vertex count, face count, bounding box extents, file size
- **Topology**: watertight status, boundary edges (holes), non-manifold edges, connected components
- **Printability**: volume (if watertight), overall printable/not printable verdict

```python
import trimesh
import numpy as np
import os

mesh = trimesh.load("FILE_PATH", force="mesh")

edges_sorted = np.sort(mesh.edges, axis=1)
ue, ec = np.unique(edges_sorted, axis=0, return_counts=True)
boundary = int((ec == 1).sum())
non_manifold = int((ec > 2).sum())
components = mesh.split(only_watertight=False)

# Print report
# Check printability issues
# Suggest fixes using snapsolid options
```

### 3. Report

Present results clearly. If the mesh has issues, suggest which Snapsolid options could fix them:

| Problem | Suggested fix |
|---------|--------------|
| Not watertight | Run full pipeline with `--cleaning-preset standard` |
| Too many faces | `--decimate --decimate-target N` |
| Multiple components | Pipeline auto-removes fragments |
| Non-manifold edges | `--cleaning-preset aggressive` |
| No flat base | Pipeline adds one by default (`--base-mode wrap` or `crop`) |
