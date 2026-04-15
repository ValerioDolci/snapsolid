"""Rectangular base construction and drone mesh post-processing.

Functions:
- add_rectangular_base: smooth walls (4 pure rectangles) + floor + watertight junction
- planar_flatten: region growing + projection onto mean plane (rectangular buildings)
- decimate_mesh: quadric edge collapse preserving shape
"""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np
import trimesh

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rectangular base v3 — smooth walls, automatic orientation
# ---------------------------------------------------------------------------

def add_rectangular_base(
    mesh: trimesh.Trimesh,
    *,
    margin: float = 2.0,
    base_height: float = 5.0,
    grid_snap: float = 5.0,
    auto_orient: bool = True,
) -> trimesh.Trimesh:
    """Add a watertight rectangular base to the mesh.

    Builds 4 smooth walls (2 triangles each) + flat floor.
    The junction connects boundary vertices to the 4 rectangle corners.
    Automatically closes small holes.

    Args:
        mesh: input mesh (typically from Object Capture / photogrammetry)
        margin: margin around boundary (mesh units)
        base_height: base height below the lowest boundary point
        grid_snap: grid snapping (only if auto_orient=False)
        auto_orient: if True, compute minimum bounding rectangle (MBR) rotated;
                     if False, use axis-aligned rectangle with grid_snap

    Returns:
        Mesh with closed rectangular base and smooth walls.
    """
    # 0. Fix pre-existing NM edges (before searching boundary loops)
    mesh = _fix_non_manifold(mesh)

    # 1. Find boundary loops
    loops = _find_boundary_loops(mesh)
    if not loops:
        logger.warning("No boundary loop found, mesh already closed?")
        return mesh

    big_loop = loops[0]
    logger.info("Main boundary: %d vertices, %d small holes",
                len(big_loop), len(loops) - 1)

    # 2. Half-edge set for consistent winding
    half_edge_set = set()
    for face in mesh.faces:
        for k in range(3):
            half_edge_set.add((int(face[k]), int(face[(k + 1) % 3])))

    # 3. Compute rectangle
    boundary_xz = mesh.vertices[big_loop][:, [0, 2]]

    if auto_orient:
        center_xz, axes, half_widths = _minimum_bounding_rectangle(boundary_xz)
        half_widths = half_widths + margin
        logger.info("MBR: center=(%.1f, %.1f), angle=%.1f°, size=%.1f x %.1f",
                     center_xz[0], center_xz[1],
                     np.degrees(np.arctan2(axes[0][1], axes[0][0])),
                     half_widths[0] * 2, half_widths[1] * 2)
    else:
        v = mesh.vertices
        x_min = np.floor((v[:, 0].min() - margin) / grid_snap) * grid_snap
        x_max = np.ceil((v[:, 0].max() + margin) / grid_snap) * grid_snap
        z_min = np.floor((v[:, 2].min() - margin) / grid_snap) * grid_snap
        z_max = np.ceil((v[:, 2].max() + margin) / grid_snap) * grid_snap
        center_xz = np.array([(x_min + x_max) / 2, (z_min + z_max) / 2])
        axes = np.array([[1.0, 0.0], [0.0, 1.0]])
        half_widths = np.array([(x_max - x_min) / 2, (z_max - z_min) / 2])
        logger.info("Axis-aligned: X=[%.1f, %.1f] Z=[%.1f, %.1f]",
                     x_min, x_max, z_min, z_max)

    # 4. Y levels
    y_wall_top = float(mesh.vertices[big_loop, 1].min())
    y_base = y_wall_top - base_height
    logger.info("Y wall_top=%.2f, y_base=%.2f", y_wall_top, y_base)

    # 5. Corner positions (3D)
    # SW=(-,-), SE=(+,-), NE=(+,+), NW=(-,+) in local coordinates (U, V)
    corner_signs = {'SW': (-1, -1), 'SE': (1, -1), 'NE': (1, 1), 'NW': (-1, 1)}

    def _corner_3d(u_sign, v_sign, y):
        xz = (center_xz
              + u_sign * half_widths[0] * axes[0]
              + v_sign * half_widths[1] * axes[1])
        return [float(xz[0]), y, float(xz[1])]

    # 6. Build geometry
    verts_list = list(mesh.vertices)
    faces_list = list(mesh.faces)

    def add_face(f):
        """Add face only if non-degenerate (all vertices distinct)."""
        if f[0] != f[1] and f[1] != f[2] and f[0] != f[2]:
            faces_list.append(f)

    # 6a. Corner vertices (4 top + 4 bot = 8 vertices)
    ct = {}  # corner top indices
    cb = {}  # corner bot indices
    for name, (us, vs) in corner_signs.items():
        ct[name] = len(verts_list)
        verts_list.append(_corner_3d(us, vs, y_wall_top))
        cb[name] = len(verts_list)
        verts_list.append(_corner_3d(us, vs, y_base))

    # 6b. For each boundary vertex: create a vertex projected to y_wall_top
    # (vertical tent: boundary vertex → projection down → junction to corner)
    proj_map = {}  # boundary_vert_idx -> projected_vert_idx
    corner_map = {}

    for vi in big_loop:
        bv = mesh.vertices[vi]
        # Project vertically: same X,Z but Y = y_wall_top
        proj_idx = len(verts_list)
        verts_list.append([float(bv[0]), y_wall_top, float(bv[2])])
        proj_map[vi] = proj_idx

        # Compute nearest corner (for horizontal junction projection → corner)
        pt_xz = np.array([bv[0], bv[2]])
        local = pt_xz - center_xz
        u = float(local @ axes[0])
        v_coord = float(local @ axes[1])

        dists = {
            0: abs(u - (-half_widths[0])),
            1: abs(u - half_widths[0]),
            2: abs(v_coord - (-half_widths[1])),
            3: abs(v_coord - half_widths[1]),
        }
        side = min(dists, key=dists.get)

        if side == 0:
            corner_map[vi] = 'SW' if v_coord < 0 else 'NW'
        elif side == 1:
            corner_map[vi] = 'SE' if v_coord < 0 else 'NE'
        elif side == 2:
            corner_map[vi] = 'SW' if u < 0 else 'SE'
        else:
            corner_map[vi] = 'NW' if u < 0 else 'NE'

    # 6c. Vertical tent: for each boundary edge, create 2 triangles
    # descending from boundary vertex to its projection at y_wall_top
    for i in range(len(big_loop)):
        a = big_loop[i]
        b = big_loop[(i + 1) % len(big_loop)]
        pa = proj_map[a]
        pb = proj_map[b]
        in_he = (a, b) in half_edge_set

        # Quad a→b→pb→pa (vertical tent), split into 2 triangles
        if in_he:
            add_face([b, a, pa])
            add_face([b, pa, pb])
        else:
            add_face([a, b, pb])
            add_face([a, pb, pa])

    # 6d. Horizontal junction: projections → corner top (fan at y_wall_top)
    for i in range(len(big_loop)):
        a = big_loop[i]
        b = big_loop[(i + 1) % len(big_loop)]
        pa = proj_map[a]
        pb = proj_map[b]
        c_a = corner_map[a]
        c_b = corner_map[b]

        if c_a == c_b:
            c = ct[c_a]
            # pa → pb → corner (horizontal triangle at y_wall_top)
            add_face([pb, pa, c])
        else:
            ca_idx = ct[c_a]
            cb_idx = ct[c_b]
            add_face([pb, pa, ca_idx])
            add_face([pb, ca_idx, cb_idx])

    # 6e. Walls: 4 pure rectangles (2 triangles each, normals facing outward)
    # Side 0 (-U): SW→NW, normal = -axes[0]
    add_face([cb['SW'], cb['NW'], ct['NW']])
    add_face([cb['SW'], ct['NW'], ct['SW']])
    # Side 1 (+U): NE→SE, normal = +axes[0]
    add_face([cb['NE'], cb['SE'], ct['SE']])
    add_face([cb['NE'], ct['SE'], ct['NE']])
    # Side 2 (-V): SE→SW, normal = -axes[1]
    add_face([cb['SE'], cb['SW'], ct['SW']])
    add_face([cb['SE'], ct['SW'], ct['SE']])
    # Side 3 (+V): NW→NE, normal = +axes[1]
    add_face([cb['NW'], cb['NE'], ct['NE']])
    add_face([cb['NW'], ct['NE'], ct['NW']])

    # 6f. Floor (2 triangles, normal -Y)
    add_face([cb['SW'], cb['NE'], cb['NW']])
    add_face([cb['SW'], cb['SE'], cb['NE']])

    logger.info("Vertical tent + junction + 4 walls + floor")

    # 7. Close small holes (fan triangulation with correct winding)
    for loop in loops[1:]:
        loop_verts = mesh.vertices[loop]
        centroid = loop_verts.mean(axis=0)
        c_idx = len(verts_list)
        verts_list.append(centroid)
        for j in range(len(loop)):
            la = loop[j]
            lb = loop[(j + 1) % len(loop)]
            if (la, lb) in half_edge_set:
                add_face([lb, la, c_idx])
            else:
                add_face([la, lb, c_idx])

    # 8. Assemble
    result = trimesh.Trimesh(
        vertices=np.array(verts_list),
        faces=np.array(faces_list),
        process=False,
    )
    result.fix_normals()

    logger.info(
        "Base added: %d → %d faces, watertight=%s",
        len(mesh.faces), len(result.faces), result.is_watertight,
    )

    return result


def _fix_non_manifold(mesh):
    """Remove faces causing non-manifold edges (shared by >2 faces).

    For each NM edge, removes the face with smallest area.
    Repeats until NM=0 or no progress.
    """
    for iteration in range(10):
        edges_sorted = np.sort(mesh.edges, axis=1)
        unique_edges, edge_counts = np.unique(
            edges_sorted, axis=0, return_counts=True
        )
        nm_edges = unique_edges[edge_counts > 2]
        if len(nm_edges) == 0:
            break

        # Find faces on NM edges, mark the smallest for removal
        nm_set = set(map(tuple, nm_edges))
        face_areas = mesh.area_faces
        remove = set()

        for fi, face in enumerate(mesh.faces):
            for k in range(3):
                e = tuple(sorted((int(face[k]), int(face[(k + 1) % 3]))))
                if e in nm_set:
                    remove.add(fi)
                    break

        if not remove:
            break

        # For each NM edge, keep the 2 largest faces, remove the rest
        edge_faces = defaultdict(list)
        for fi in remove:
            face = mesh.faces[fi]
            for k in range(3):
                e = tuple(sorted((int(face[k]), int(face[(k + 1) % 3]))))
                if e in nm_set:
                    edge_faces[e].append(fi)

        to_remove = set()
        for e, fis in edge_faces.items():
            if len(fis) <= 2:
                continue
            fis_sorted = sorted(fis, key=lambda f: face_areas[f], reverse=True)
            to_remove.update(fis_sorted[2:])

        if not to_remove:
            break

        keep = np.ones(len(mesh.faces), dtype=bool)
        keep[list(to_remove)] = False
        mesh = trimesh.Trimesh(
            vertices=mesh.vertices,
            faces=mesh.faces[keep],
            process=False,
        )
        mesh.fix_normals()
        logger.info("Fix NM iter %d: removed %d faces, NM remaining=%d",
                     iteration, len(to_remove),
                     (np.unique(np.sort(mesh.edges, axis=1), axis=0,
                                return_counts=True)[1] > 2).sum())

    return mesh


def _find_boundary_loops(mesh):
    """Find all boundary loops (open edges) of the mesh."""
    edges_sorted = np.sort(mesh.edges, axis=1)
    unique_edges, edge_counts = np.unique(edges_sorted, axis=0, return_counts=True)
    boundary = unique_edges[edge_counts == 1]

    if len(boundary) == 0:
        return []

    adj = defaultdict(list)
    for e in boundary:
        adj[int(e[0])].append(int(e[1]))
        adj[int(e[1])].append(int(e[0]))

    visited = set()
    loops = []
    for start in sorted(adj.keys()):
        if start in visited:
            continue
        loop = []
        current = start
        prev = None
        while current not in visited:
            visited.add(current)
            loop.append(current)
            neighbors = [n for n in adj[current] if n != prev]
            if not neighbors:
                break
            prev = current
            current = neighbors[0]
        loops.append(loop)

    loops.sort(key=len, reverse=True)
    return loops


def _minimum_bounding_rectangle(points_2d):
    """Compute the minimum area rectangle containing 2D points.

    Uses convex hull + rotating calipers.

    Returns:
        center: rectangle center (2,)
        axes: two orthonormal axes (2x2 array, rows = axis vectors)
        half_widths: half-width along each axis (2,)
    """
    from scipy.spatial import ConvexHull

    hull = ConvexHull(points_2d)
    hull_pts = points_2d[hull.vertices]
    n = len(hull_pts)

    min_area = float('inf')
    best = None

    for i in range(n):
        edge = hull_pts[(i + 1) % n] - hull_pts[i]
        edge_len = np.linalg.norm(edge)
        if edge_len < 1e-10:
            continue
        edge = edge / edge_len
        # Perpendicular (90° CCW rotation → right-handed system)
        perp = np.array([-edge[1], edge[0]])

        proj_e = hull_pts @ edge
        proj_p = hull_pts @ perp

        min_e, max_e = proj_e.min(), proj_e.max()
        min_p, max_p = proj_p.min(), proj_p.max()

        area = (max_e - min_e) * (max_p - min_p)
        if area < min_area:
            min_area = area
            best = (edge, perp, min_e, max_e, min_p, max_p)

    if best is None:
        # Degenerate hull (collinear points) — fallback to axis-aligned bounding box
        min_vals = points_2d.min(axis=0)
        max_vals = points_2d.max(axis=0)
        center = (min_vals + max_vals) / 2
        hw = (max_vals - min_vals) / 2
        axes = np.eye(2)
        return center, axes, hw

    edge, perp, min_e, max_e, min_p, max_p = best
    center = ((min_e + max_e) / 2) * edge + ((min_p + max_p) / 2) * perp
    hw = np.array([(max_e - min_e) / 2, (max_p - min_p) / 2])

    return center, np.array([edge, perp]), hw


# ---------------------------------------------------------------------------
# Crop mode — trim mesh to inscribed rectangle + base
# ---------------------------------------------------------------------------

def crop_mesh_to_rectangle(
    mesh: trimesh.Trimesh,
    *,
    margin: float = 0.0,
    base_height: float = 5.0,
    grid_snap: float = 5.0,
    auto_orient: bool = True,
) -> trimesh.Trimesh:
    """Trim the mesh to a rectangle and add a closed base.

    Unlike add_rectangular_base (which wraps from outside),
    this function finds the largest inscribed rectangle in the
    XZ projection of the mesh, trims everything outside,
    and builds walls + floor.

    Args:
        mesh: input mesh
        margin: negative margin (shrink) from bounding box (default: 0)
        base_height: base wall height below the lowest point
        grid_snap: grid snapping (only if auto_orient=False)
        auto_orient: if True use MBR, if False axis-aligned

    Returns:
        Trimmed mesh with closed rectangular base.
    """
    # 1. Compute cutting rectangle (bounding box - margin, inscribed in mesh)
    v = mesh.vertices
    if auto_orient:
        boundary_xz = v[:, [0, 2]]
        center_xz, axes, half_widths = _minimum_bounding_rectangle(boundary_xz)
        # Shrink to stay inside the mesh
        half_widths = half_widths - margin
        logger.info("Crop MBR: center=(%.1f, %.1f), size=%.1f x %.1f",
                     center_xz[0], center_xz[1],
                     half_widths[0] * 2, half_widths[1] * 2)
    else:
        x_min = np.ceil((v[:, 0].min() + margin) / grid_snap) * grid_snap
        x_max = np.floor((v[:, 0].max() - margin) / grid_snap) * grid_snap
        z_min = np.ceil((v[:, 2].min() + margin) / grid_snap) * grid_snap
        z_max = np.floor((v[:, 2].max() - margin) / grid_snap) * grid_snap
        center_xz = np.array([(x_min + x_max) / 2, (z_min + z_max) / 2])
        axes = np.array([[1.0, 0.0], [0.0, 1.0]])
        half_widths = np.array([(x_max - x_min) / 2, (z_max - z_min) / 2])
        logger.info("Crop axis-aligned: X=[%.1f, %.1f] Z=[%.1f, %.1f]",
                     x_min, x_max, z_min, z_max)

    # 2. Cut with 4 planes (slice_plane removes the part on the negative side of the normal)
    # The 4 planes correspond to the 4 sides of the rectangle
    axis_u = np.array([axes[0][0], 0.0, axes[0][1]])  # 3D da XZ
    axis_v = np.array([axes[1][0], 0.0, axes[1][1]])
    center_3d = np.array([center_xz[0], 0.0, center_xz[1]])

    planes = [
        # (point on plane, inward normal)
        (center_3d - half_widths[0] * axis_u, axis_u),      # side -U: keep +U
        (center_3d + half_widths[0] * axis_u, -axis_u),     # side +U: keep -U
        (center_3d - half_widths[1] * axis_v, axis_v),      # side -V: keep +V
        (center_3d + half_widths[1] * axis_v, -axis_v),     # side +V: keep -V
    ]

    faces_before = len(mesh.faces)
    for origin, normal in planes:
        try:
            mesh = mesh.slice_plane(origin, normal, cached_dots=None)
        except Exception as e:
            logger.warning("slice_plane error: %s", e)
            continue
        if mesh is None or len(mesh.faces) == 0:
            logger.error("Slice removed the entire mesh!")
            return mesh

    logger.info("Crop: %d → %d faces after trimming", faces_before, len(mesh.faces))

    # 3. Post-slice cleanup: merge close vertices + remove degenerate
    import pymeshlab
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    ms = pymeshlab.MeshSet()
    with NamedTemporaryFile(suffix=".ply", delete=False) as f:
        tmp = Path(f.name)
    try:
        mesh.export(str(tmp))
        ms.load_new_mesh(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)

    ms.apply_filter("meshing_merge_close_vertices", threshold=pymeshlab.PercentageValue(0.001))
    ms.apply_filter("meshing_remove_duplicate_faces")
    ms.apply_filter("meshing_remove_null_faces")
    ms.apply_filter("meshing_repair_non_manifold_edges")
    ms.apply_filter("meshing_repair_non_manifold_vertices")
    ms.apply_filter("meshing_close_holes", maxholesize=200)

    with NamedTemporaryFile(suffix=".ply", delete=False) as f:
        tmp = Path(f.name)
    try:
        ms.save_current_mesh(str(tmp))
        mesh = trimesh.load(str(tmp), force="mesh")
    finally:
        tmp.unlink(missing_ok=True)
    logger.info("Post-slice cleanup: %d faces", len(mesh.faces))

    # 4. Use add_rectangular_base to build base on main boundary
    result = add_rectangular_base(
        mesh,
        margin=0.0,
        base_height=base_height,
        grid_snap=grid_snap,
        auto_orient=auto_orient,
    )

    logger.info(
        "Crop + base: %d → %d faces, watertight=%s",
        faces_before, len(result.faces), result.is_watertight,
    )
    return result


# ---------------------------------------------------------------------------
# Scale to mm
# ---------------------------------------------------------------------------

def scale_mesh_to_mm(
    mesh: trimesh.Trimesh,
    target_max_mm: float,
) -> trimesh.Trimesh:
    """Uniformly scale the mesh so that the longest side = target_max_mm.

    Args:
        mesh: input mesh
        target_max_mm: desired maximum dimension in mm

    Returns:
        Scaled mesh.
    """
    extents = mesh.extents  # [dx, dy, dz]
    max_extent = extents.max()
    if max_extent < 1e-10:
        logger.warning("Mesh with zero extent, skip scaling")
        return mesh

    scale_factor = target_max_mm / max_extent
    mesh.apply_scale(scale_factor)

    logger.info(
        "Scale: %.4fx (max extent %.1f → %.1f mm, extents=%.1f x %.1f x %.1f)",
        scale_factor, max_extent, target_max_mm,
        mesh.extents[0], mesh.extents[1], mesh.extents[2],
    )
    return mesh


# ---------------------------------------------------------------------------
# Planar flatten and decimation
# ---------------------------------------------------------------------------

def planar_flatten(
    mesh: trimesh.Trimesh,
    *,
    angle_threshold: float = 15.0,
    min_region_faces: int = 50,
    strength: float = 0.7,
) -> trimesh.Trimesh:
    """Flatten planar regions of the mesh (roofs, walls, flat surfaces).

    Region growing by similar normals, then project vertices onto mean plane.
    Makes buildings more rectangular without touching detail areas.

    Args:
        mesh: input mesh
        angle_threshold: max angle between normals for same region (degrees)
        min_region_faces: minimum faces to consider a region
        strength: projection strength (0 = none, 1 = full)

    Returns:
        Mesh with flattened planar surfaces.
    """
    face_normals = mesh.face_normals
    face_adjacency = mesh.face_adjacency

    # Adjacency list for faces
    face_adj = defaultdict(list)
    for pair in face_adjacency:
        face_adj[pair[0]].append(pair[1])
        face_adj[pair[1]].append(pair[0])

    # Region growing
    cos_threshold = np.cos(np.radians(angle_threshold))
    face_labels = np.full(len(mesh.faces), -1, dtype=int)
    region_sizes = {}
    region_id = 0

    for seed in range(len(mesh.faces)):
        if face_labels[seed] != -1:
            continue
        queue = [seed]
        face_labels[seed] = region_id
        seed_normal = face_normals[seed]
        count = 1
        while queue:
            current = queue.pop(0)
            for neighbor in face_adj[current]:
                if face_labels[neighbor] != -1:
                    continue
                if np.dot(face_normals[neighbor], seed_normal) > cos_threshold:
                    face_labels[neighbor] = region_id
                    queue.append(neighbor)
                    count += 1
        region_sizes[region_id] = count
        region_id += 1

    large_regions = sum(1 for s in region_sizes.values() if s >= min_region_faces)
    logger.info(
        "Planar flatten: %d total regions, %d large (>=%d faces)",
        region_id, large_regions, min_region_faces,
    )

    # Project vertices onto mean plane for large regions
    new_verts = mesh.vertices.copy()
    vert_contributions = defaultdict(list)

    for rid, size in region_sizes.items():
        if size < min_region_faces:
            continue
        region_mask = face_labels == rid
        region_face_indices = np.where(region_mask)[0]
        region_vert_indices = np.unique(mesh.faces[region_face_indices].flatten())
        region_verts = mesh.vertices[region_vert_indices]

        centroid = region_verts.mean(axis=0)
        centered = region_verts - centroid
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        plane_normal = vh[2]

        for vi in region_vert_indices:
            dist = np.dot(mesh.vertices[vi] - centroid, plane_normal)
            vert_contributions[vi].append((plane_normal, dist, size))

    flattened = 0
    for vi, contributions in vert_contributions.items():
        total_weight = sum(w for _, _, w in contributions)
        avg_dist = sum(d * w for _, d, w in contributions) / total_weight
        avg_normal = sum(n * w for n, _, w in contributions) / total_weight
        avg_normal = avg_normal / (np.linalg.norm(avg_normal) + 1e-10)
        new_verts[vi] -= avg_dist * avg_normal * strength
        flattened += 1

    logger.info("Planar flatten: %d vertices projected (strength=%.1f)", flattened, strength)

    result = trimesh.Trimesh(vertices=new_verts, faces=mesh.faces, process=False)
    result.fix_normals()
    return result


def decimate_mesh(
    mesh: trimesh.Trimesh,
    target_faces: int = 1_000_000,
) -> trimesh.Trimesh:
    """Decimate the mesh with quadric edge collapse preserving shape.

    Args:
        mesh: input mesh
        target_faces: target number of faces

    Returns:
        Decimated mesh.
    """
    if len(mesh.faces) <= target_faces:
        logger.info("Mesh already below target (%d <= %d), skip decimation",
                     len(mesh.faces), target_faces)
        return mesh

    import pymeshlab
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    ms = pymeshlab.MeshSet()
    with NamedTemporaryFile(suffix=".ply", delete=False) as f:
        tmp = Path(f.name)
    try:
        mesh.export(str(tmp))
        ms.load_new_mesh(str(tmp))
    finally:
        tmp.unlink(missing_ok=True)

    faces_before = ms.current_mesh().face_number()
    ms.apply_filter(
        "meshing_decimation_quadric_edge_collapse",
        targetfacenum=target_faces,
        preserveboundary=True,
        preservenormal=True,
        preservetopology=True,
        qualitythr=0.5,
    )
    faces_after = ms.current_mesh().face_number()

    with NamedTemporaryFile(suffix=".ply", delete=False) as f:
        tmp = Path(f.name)
    try:
        ms.save_current_mesh(str(tmp))
        result = trimesh.load(str(tmp), force="mesh")
    finally:
        tmp.unlink(missing_ok=True)

    logger.info("Decimation: %d → %d faces", faces_before, faces_after)
    return result
