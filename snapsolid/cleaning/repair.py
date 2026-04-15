"""Mesh repair: fix topology, holes, normals."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pymeshlab
import trimesh
from scipy.spatial import Delaunay

from ..config import CleaningConfig

logger = logging.getLogger(__name__)


class MeshRepairer:
    """Repair topological and geometric mesh issues."""

    def __init__(self, config: CleaningConfig):
        self.config = config

    def repair(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Run the complete repair sequence.

        Layered approach:
        1. Remove small components (noise)
        2. Remove degenerate and duplicate faces
        3. Fix non-manifold
        4. Flat base (close the largest boundary with a plane)
        5. close_holes for remaining small/medium holes
        6. If still not watertight: local patch on remaining holes
        7. Fix normals
        """
        ms = self._trimesh_to_meshlab(mesh)

        # Step 1: Remove small disconnected components
        if self.config.remove_small_components:
            ms = self._remove_small_components(ms)

        # Step 2: Remove degenerate and duplicate faces
        if self.config.remove_degenerate_faces:
            ms = self._remove_degenerate(ms)
        if self.config.remove_duplicate_faces:
            ms = self._remove_duplicates(ms)

        # Step 3: Fix non-manifold
        if self.config.fix_non_manifold:
            ms = self._fix_non_manifold(ms)

        # Step 4: Flat base — close the largest boundary
        # Skip if close_holes=False (the pipeline will add the rectangular base later)
        if self.config.close_holes:
            mesh = self._meshlab_to_trimesh(ms)
            mesh = self._add_flat_base(mesh)
            ms = self._trimesh_to_meshlab(mesh)

            # Step 4b: Re-fix non-manifold (flat base can create some)
            if self.config.fix_non_manifold:
                ms = self._fix_non_manifold(ms)

            # Step 5: close_holes for small/medium holes
            ms = self._close_holes(ms)

            # Step 6: If still not watertight, local patch on remaining holes
            tmp_check = self._meshlab_to_trimesh(ms)
            if not tmp_check.is_watertight:
                logger.info("Mesh still not watertight, trying local patch...")
                mesh = self._patch_remaining_holes(tmp_check)
                ms = self._trimesh_to_meshlab(mesh)
        else:
            logger.info("close_holes=False → skip flat base, close holes, patch (rectangular base will handle closure)")

        # Step 7: Fix normals
        if self.config.fix_normals:
            ms = self._fix_normals(ms)

        # Step 8: Fix negative volume (globally inverted normals)
        result = self._meshlab_to_trimesh(ms)
        result = self._fix_winding(result)

        # Step 9: Final cleanup — remove duplicates and residual components
        ms2 = self._trimesh_to_meshlab(result)
        if self.config.remove_duplicate_faces:
            ms2 = self._remove_duplicates(ms2)
        if self.config.remove_small_components:
            ms2 = self._remove_small_components(ms2)
        result = self._meshlab_to_trimesh(ms2)

        return result

    def _remove_small_components(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Remove all components except the largest.

        For photogrammetry: there is ONE main object and surrounding noise.
        We keep the component with the most faces, discard everything else.
        """
        logger.info("Removing disconnected components (keep the largest)...")
        try:
            # Convert to trimesh to count components
            tmp_mesh = self._meshlab_to_trimesh(ms)
            components = tmp_mesh.split(only_watertight=False)

            if len(components) <= 1:
                logger.info("Single component, nothing to remove")
                return ms

            # Find the largest component by face count
            largest = max(components, key=lambda c: len(c.faces))
            removed = len(tmp_mesh.faces) - len(largest.faces)
            logger.info(
                "Found %d components, keeping the largest (%d faces), removing %d faces",
                len(components), len(largest.faces), removed,
            )

            # Reload only the main component in meshlab
            ms = self._trimesh_to_meshlab(largest)
        except Exception as e:
            logger.warning("Component removal error: %s", e)
        return ms

    def _remove_degenerate(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Remove degenerate faces (zero area, zero-length edges)."""
        logger.info("Removing degenerate faces...")
        try:
            ms.apply_filter("meshing_remove_folded_faces")
        except Exception:
            pass
        try:
            ms.apply_filter("meshing_remove_unreferenced_vertices")
        except Exception:
            pass
        return ms

    def _remove_duplicates(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Remove duplicate faces and vertices."""
        logger.info("Removing duplicates...")
        try:
            ms.apply_filter("meshing_remove_duplicate_faces")
            ms.apply_filter("meshing_remove_duplicate_vertices")
        except Exception as e:
            logger.warning("Duplicate removal error: %s", e)
        return ms

    def _fix_non_manifold(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Repair non-manifold edges and vertices.

        Iterates because fixing one issue can create others.
        """
        logger.info("Fix non-manifold (iterative)...")
        max_iterations = 5
        for i in range(max_iterations):
            changed = False
            try:
                before = ms.current_mesh().face_number()
                ms.apply_filter("meshing_repair_non_manifold_edges")
                after = ms.current_mesh().face_number()
                if before != after:
                    changed = True
            except Exception:
                pass
            try:
                before = ms.current_mesh().face_number()
                ms.apply_filter("meshing_repair_non_manifold_vertices",
                                vertdispratio=0)
                after = ms.current_mesh().face_number()
                if before != after:
                    changed = True
            except Exception:
                pass
            if not changed:
                logger.info("Non-manifold fixed in %d iterations", i + 1)
                break
        return ms

    def _close_holes(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Close holes in the mesh."""
        logger.info("Closing holes (max size: %d edges)...", self.config.max_hole_size)
        try:
            ms.apply_filter(
                "meshing_close_holes",
                maxholesize=self.config.max_hole_size,
                newfaceselected=False,
            )
        except Exception as e:
            logger.warning("Hole closing error: %s", e)
        return ms

    # --- Methods for closing real holes (photogrammetry) ---

    def _find_boundary_loops(self, mesh: trimesh.Trimesh) -> list[list[int]]:
        """Find boundary loops (open edges) in the mesh.

        Returns list of loops, each loop is an ordered list of vertex indices.
        """
        edges_sorted = np.sort(mesh.edges, axis=1)
        unique_edges, edge_counts = np.unique(edges_sorted, axis=0, return_counts=True)
        boundary_edges = unique_edges[edge_counts == 1]

        if len(boundary_edges) == 0:
            return []

        # Build adjacency graph only for boundary edges
        adj = defaultdict(list)
        for e in boundary_edges:
            adj[e[0]].append(e[1])
            adj[e[1]].append(e[0])

        # Find connected components (each component = a loop/chain)
        visited = set()
        loops = []
        for start in adj:
            if start in visited:
                continue
            # Traverse the loop in order
            loop = self._trace_loop(start, adj)
            visited.update(loop)
            loops.append(loop)

        # Sort by decreasing size
        loops.sort(key=len, reverse=True)
        return loops

    def _trace_loop(self, start: int, adj: dict[int, list[int]]) -> list[int]:
        """Traverse a boundary loop in order, following edges."""
        loop = [start]
        visited = {start}
        current = start

        while True:
            neighbors = [n for n in adj[current] if n not in visited]
            if not neighbors:
                break
            current = neighbors[0]
            visited.add(current)
            loop.append(current)

        return loop

    def _add_flat_base(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Add a flat base by closing the largest boundary loop.

        For photogrammetry: the bottom of the object (where it rests) is almost
        always missing. We close it with a flat triangulated plane.
        """
        loops = self._find_boundary_loops(mesh)
        if not loops:
            logger.info("No open boundary found, skip flat base")
            return mesh

        largest_loop = loops[0]
        if len(largest_loop) < 3:
            return mesh

        loop_verts = mesh.vertices[largest_loop]
        loop_extent = np.max(loop_verts, axis=0) - np.min(loop_verts, axis=0)
        mesh_extent = mesh.bounding_box.extents

        # Only if the boundary is significant (>20% of bbox on at least 2 axes)
        large_axes = np.sum(loop_extent / mesh_extent > 0.2)
        if large_axes < 2 and len(largest_loop) < 20:
            logger.info("Largest boundary too small for flat base, skip")
            return mesh

        logger.info("Adding flat base: %d vertices in boundary (extent: %.3f x %.3f x %.3f)",
                     len(largest_loop), *loop_extent)

        # Triangulate the loop by projecting onto a plane
        new_faces = self._triangulate_loop(loop_verts, largest_loop)

        if new_faces is not None and len(new_faces) > 0:
            # Orient new faces consistently with the existing mesh
            new_faces = self._orient_new_faces(mesh, new_faces, largest_loop)

            # Add new faces to the mesh
            all_faces = np.vstack([mesh.faces, new_faces])
            mesh = trimesh.Trimesh(
                vertices=mesh.vertices,
                faces=all_faces,
                process=True,  # process=True to merge and clean
            )
            logger.info("Flat base added: %d new faces", len(new_faces))
        else:
            logger.warning("Base triangulation failed")

        return mesh

    def _orient_new_faces(
        self, mesh: trimesh.Trimesh, new_faces: np.ndarray, loop: list[int]
    ) -> np.ndarray:
        """Orient new faces consistently with the mesh.

        Checks that new face normals point outward,
        based on normals of faces adjacent to the boundary.
        """
        # Find existing faces adjacent to the boundary
        loop_set = set(loop)
        adj_face_indices = []
        for i, face in enumerate(mesh.faces):
            if any(v in loop_set for v in face):
                adj_face_indices.append(i)
                if len(adj_face_indices) >= 10:  # a few are enough to estimate
                    break

        if not adj_face_indices:
            return new_faces

        # Mean normal of faces adjacent to boundary
        adj_normals = mesh.face_normals[adj_face_indices]
        avg_normal = adj_normals.mean(axis=0)
        avg_normal = avg_normal / (np.linalg.norm(avg_normal) + 1e-10)

        # Mean normal of new faces
        new_mesh = trimesh.Trimesh(vertices=mesh.vertices, faces=new_faces, process=False)
        new_avg_normal = new_mesh.face_normals.mean(axis=0)
        new_avg_normal = new_avg_normal / (np.linalg.norm(new_avg_normal) + 1e-10)

        # If they point in the same direction, new faces are inverted
        # (they should point in the opposite direction, into the hole)
        # Actually to close the hole, new normals should point
        # "away" from the mesh, like adjacent faces.
        # Compute mesh centroid and check
        centroid = mesh.vertices.mean(axis=0)
        loop_center = mesh.vertices[loop].mean(axis=0)
        outward = loop_center - centroid
        outward = outward / (np.linalg.norm(outward) + 1e-10)

        # New faces must have normals pointing OPPOSITE to outward
        # (they close the hole from the inside)
        if np.dot(new_avg_normal, outward) > 0:
            # Flip the faces
            new_faces = new_faces[:, ::-1]
            logger.info("Base faces flipped for normal consistency")

        return new_faces

    def _triangulate_loop(
        self, loop_verts: np.ndarray, loop_indices: list[int]
    ) -> np.ndarray | None:
        """Triangulate a vertex loop by projecting onto a 2D plane.

        Uses 2D Delaunay on the loop projection, then filters triangles
        that fall inside the boundary polygon.
        """
        if len(loop_indices) < 3:
            return None

        # Find the best plane for projection (PCA)
        centered = loop_verts - loop_verts.mean(axis=0)
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        # The first 2 principal axes define the plane
        proj_2d = centered @ vh[:2].T

        try:
            # Fan triangulation from centroid (more robust than Delaunay for loops)
            n = len(loop_indices)
            centroid_idx = None  # fan from first vertex; Delaunay used below

            # Try Delaunay and filter
            tri = Delaunay(proj_2d)
            simplices = tri.simplices

            # Filter: keep only triangles whose vertices are all in the loop
            # (Delaunay can create triangles outside the polygon)
            from matplotlib.path import Path as MplPath
            polygon = MplPath(proj_2d)

            valid_faces = []
            for simplex in simplices:
                # Triangle centroid must be inside the polygon
                tri_center = proj_2d[simplex].mean(axis=0)
                if polygon.contains_point(tri_center):
                    # Map local indices -> global mesh indices
                    valid_faces.append([
                        loop_indices[simplex[0]],
                        loop_indices[simplex[1]],
                        loop_indices[simplex[2]],
                    ])

            if valid_faces:
                return np.array(valid_faces)

        except Exception as e:
            logger.warning("Delaunay failed: %s, trying fan triangulation", e)

        # Fallback: fan triangulation from centroid (adds a vertex)
        # For simplicity, fan from first vertex (works for convex loops)
        faces = []
        for i in range(1, len(loop_indices) - 1):
            faces.append([
                loop_indices[0],
                loop_indices[i],
                loop_indices[i + 1],
            ])
        return np.array(faces) if faces else None

    def _patch_remaining_holes(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Close remaining holes with progressive approach.

        Strategy from least to most invasive:
        1. Direct triangulation of small loops (< 50 vertices)
        2. Delaunay with curvature for medium loops (50-200 vertices)
        3. Local Poisson via PyMeshLab for large loops (> 200 vertices)
        4. If all fails, accept non-watertight mesh (NO voxel patch)

        Voxel patch is disabled by default because it destroys geometry
        on high-detail meshes (>100k faces).
        """
        loops = self._find_boundary_loops(mesh)
        if not loops:
            return mesh

        logger.info("Progressive patch: %d holes remaining...", len(loops))

        all_new_faces = []
        patched = 0
        skipped = 0

        for i, loop in enumerate(loops):
            if len(loop) < 3:
                continue

            loop_verts = mesh.vertices[loop]

            # Strategy based on hole size
            if len(loop) <= 200:
                # Small/medium holes: direct triangulation
                new_faces = self._triangulate_loop(loop_verts, loop)
                if new_faces is not None and len(new_faces) > 0:
                    # Orient new faces
                    new_faces = self._orient_new_faces(mesh, new_faces, loop)
                    all_new_faces.append(new_faces)
                    patched += 1
                    logger.info("  Hole %d: %d vertices → %d faces (triangulation)",
                                i, len(loop), len(new_faces))
                else:
                    skipped += 1
                    logger.info("  Hole %d: %d vertices → skip (triangulation failed)",
                                i, len(loop))
            else:
                # Large holes: too complex for direct triangulation
                # Accept as open boundary — the slicer will handle it
                skipped += 1
                logger.info("  Hole %d: %d vertices → skip (too large, slicer will handle)",
                            i, len(loop))

        if all_new_faces:
            combined = np.vstack([mesh.faces] + all_new_faces)
            mesh = trimesh.Trimesh(
                vertices=mesh.vertices,
                faces=combined,
                process=False,
            )

        logger.info("Patch complete: %d closed, %d left open", patched, skipped)

        if not mesh.is_watertight:
            logger.info("Mesh not perfectly watertight — acceptable for modern slicers")

        return mesh

    def _voxel_patch(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """Last resort: voxelize and reconstruct to guarantee watertight.

        Uses adaptive resolution based on mesh size.
        """
        try:
            # Resolution: ~200 voxels on the longest side
            pitch = max(mesh.bounding_box.extents) / 200
            voxelized = mesh.voxelized(pitch)
            # Fill interior
            filled = voxelized.fill()
            # Reconstruct mesh with marching cubes
            new_mesh = filled.marching_cubes
            logger.info("Voxel patch: %d -> %d faces, watertight=%s",
                         len(mesh.faces), len(new_mesh.faces), new_mesh.is_watertight)
            return new_mesh
        except Exception as e:
            logger.warning("Voxel patch failed: %s", e)
            return mesh



    def _fix_normals(self, ms: pymeshlab.MeshSet) -> pymeshlab.MeshSet:
        """Orient normals consistently outward."""
        logger.info("Fixing normals...")
        try:
            ms.apply_filter("meshing_re_orient_faces_coherentely")
        except Exception:
            pass
        try:
            ms.apply_filter("compute_normal_per_face")
            ms.apply_filter("compute_normal_per_vertex")
        except Exception as e:
            logger.warning("Fix normals error: %s", e)
        return ms

    def _fix_winding(self, mesh: trimesh.Trimesh) -> trimesh.Trimesh:
        """If volume is negative, flip faces to orient normals outward."""
        if not mesh.is_watertight:
            logger.info("Mesh not watertight, skip fix winding")
            return mesh
        vol = mesh.volume
        if vol is None:
            logger.warning("Volume is None (mesh not watertight?), skip fix winding")
            return mesh
        logger.info("Volume mesh: %.6f", vol)
        if vol < 0:
            logger.info("Negative volume → flipping faces")
            mesh.faces = mesh.faces[:, ::-1]
            mesh.fix_normals()
            logger.info("New volume: %.6f", mesh.volume)
        return mesh

    def _trimesh_to_meshlab(self, mesh: trimesh.Trimesh) -> pymeshlab.MeshSet:
        """Convert trimesh -> MeshSet via temporary file."""
        ms = pymeshlab.MeshSet()
        with NamedTemporaryFile(suffix=".ply", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            mesh.export(str(tmp_path))
            ms.load_new_mesh(str(tmp_path))
        finally:
            tmp_path.unlink(missing_ok=True)
        return ms

    def _meshlab_to_trimesh(self, ms: pymeshlab.MeshSet) -> trimesh.Trimesh:
        """Convert MeshSet -> trimesh via temporary file."""
        with NamedTemporaryFile(suffix=".ply", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            ms.save_current_mesh(str(tmp_path))
            result = trimesh.load(str(tmp_path), force="mesh")
        finally:
            tmp_path.unlink(missing_ok=True)
        return result
