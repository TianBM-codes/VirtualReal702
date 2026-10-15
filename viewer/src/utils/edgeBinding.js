// Match undeformed coordinates, preserving the first coincident mesh vertex.
export function buildEdgeVertexIndex(edgePosArr, meshPosArr) {
  const lookup = new Map()
  const Nv = meshPosArr.length / 3
  for (let i = 0; i < Nv; i++) {
    const k = `${Math.round(meshPosArr[i*3]*1e4)},${Math.round(meshPosArr[i*3+1]*1e4)},${Math.round(meshPosArr[i*3+2]*1e4)}`
    if (!lookup.has(k)) lookup.set(k, i)
  }
  const Ne = edgePosArr.length / 3
  const idx = new Uint32Array(Ne)
  for (let i = 0; i < Ne; i++) {
    const k = `${Math.round(edgePosArr[i*3]*1e4)},${Math.round(edgePosArr[i*3+1]*1e4)},${Math.round(edgePosArr[i*3+2]*1e4)}`
    idx[i] = lookup.get(k) ?? 0
  }
  return idx
}

// Build the association only when edges first need to follow a deformation.
// Callers discard indices whenever geometry/edges are replaced.
export function syncEdgePositions(dst, originalEdges, originalMesh, currentMesh, indices) {
  const resolved = indices ?? buildEdgeVertexIndex(originalEdges, originalMesh)
  for (let i = 0; i < resolved.length; i++) {
    const vi = resolved[i]
    dst[i*3] = currentMesh[vi*3]
    dst[i*3+1] = currentMesh[vi*3+1]
    dst[i*3+2] = currentMesh[vi*3+2]
  }
  return resolved
}
