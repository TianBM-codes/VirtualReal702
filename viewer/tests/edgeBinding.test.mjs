import test from 'node:test'
import assert from 'node:assert/strict'
import { buildEdgeVertexIndex, syncEdgePositions } from '../src/utils/edgeBinding.js'

test('coincident vertices keep first match; coordinate rounding and missing endpoints stay compatible', () => {
  const mesh = new Float32Array([1, 2, 3, 1, 2, 3, 4, 5, 6])
  const edges = new Float32Array([4.00001, 5, 6, 1, 2, 3, 99, 0, 0])
  assert.deepEqual([...buildEdgeVertexIndex(edges, mesh)], [2, 0, 0])
})

test('first deformation binds original positions, later frames reuse indices, reset restores edges', () => {
  const originalMesh = new Float32Array([0, 0, 0, 10, 0, 0, 20, 0, 0])
  const originalEdges = new Float32Array([20, 0, 0, 10, 0, 0])
  const dst = originalEdges.slice()
  const deformed = new Float32Array([1, 2, 3, 11, 4, 5, 22, 6, 7])
  const indices = syncEdgePositions(dst, originalEdges, originalMesh, deformed)
  assert.deepEqual([...dst], [22, 6, 7, 11, 4, 5])
  // Poison matching inputs: a cached association must not scan them again.
  const reused = syncEdgePositions(dst, null, null, originalMesh, indices)
  assert.equal(reused, indices)
  assert.deepEqual(dst, originalEdges)
  assert.deepEqual([...originalMesh], [0, 0, 0, 10, 0, 0, 20, 0, 0])
})

test('discarding the association after reload matches a changed vertex order', () => {
  const edges = new Float32Array([20, 0, 0, 10, 0, 0])
  const replacement = new Float32Array([20, 0, 0, 10, 0, 0])
  const dst = edges.slice()
  const indices = syncEdgePositions(dst, edges, replacement, new Float32Array([21, 0, 0, 12, 0, 0]))
  assert.deepEqual([...indices], [0, 1])
  assert.deepEqual([...dst], [21, 0, 0, 12, 0, 0])
})
