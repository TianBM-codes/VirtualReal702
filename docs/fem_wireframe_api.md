# MAC comparison wireframe API

Existing full-mesh and animation endpoints remain available. These endpoints
provide an additional FE display model using experimental trace-line edges.
Display fallback matches never update saved node matches or MAC inputs.

## Geometry

`POST /api/model/testMesh/femWireframe/geometry`

```json
{"project_id": 574, "unmatched_policy": "nearest"}
```

`unmatched_policy`: `disconnect` (default) drops edges with unavailable endpoints;
`nearest` substitutes the nearest FE node without a distance cutoff. Distances
are returned so distant substitutions can be inspected. Search uses test
coordinates after the registration stored with the existing node matches.
With no saved matches, identity registration is used.

The standard response envelope contains `data` with:

- `ids`: experimental node IDs, in display vertex order.
- `originPos`: flat FE coordinates, three numbers per vertex.
- `elementsIndex`: flat zero-based line endpoint indices, two per edge.
- `primitive`: `lines`; use indexed LineSegments, not triangular Mesh.
- `mapping`: test ID, instance, FE label, geometry row, source (`matched` or
  `nearest`), and nearest fallback distance (null for saved matches).
- `diagnostics`: unmatched IDs, skipped edge count, fallback count, duplicate
  FE mappings. Duplicate vertices and zero-length edges are retained.
- `registration`: saved experimental-to-FE transform.
- `geometry_version`: identifies geometry, topology, mapping, policy and sources.

Geometry label indices are cached with file modification/size signatures.
Nearest search trees are built lazily and bounded to two cached instance files.
Coordinates of formally matched nodes are read directly by selected HDF5 rows
from L2 global-coordinate geometry, including assembly instance transforms.
Geometry is rebuilt from current database rows on requests, so rematching and
trace-line edits are reflected without manual cache deletion.

## Modal basis and synchronization

`POST /api/model/testMesh/femWireframe/animation`

```json
{
  "project_id": 574,
  "order": 2,
  "step": "Modal",
  "fem_frame": 3,
  "result_group": "op2",
  "unmatched_policy": "nearest",
  "geometry_version": "value returned by geometry",
  "coefficient": 1.0,
  "n_frames": 20,
  "component": "usum",
  "flip": false
}
```

Pass the same FE step/frame/result group selected by the existing comparison
workflow. Defaults remain those of `syncAnimation`; this API does not change
modal pairing. `order=0` returns zero basis vectors. `flip` follows the existing
rule of flipping experimental vectors only.

Returns `geometry_version`, `test`, `fem`, `sync` in the standard `data` envelope.
`test` includes experimental geometry, real/imaginary basis and `scaleFactor`.
Its coordinates and vector directions are transformed into the FE coordinate
system using saved registration. `fem` includes availability/reason, selected
frame/frequency, the existing `scale`, `ids`, flat `real`/`imag`, and
`missing_result_node_ids`. FE basis order is exactly the geometry vertex order.
Missing node results remain stationary and are explicitly reported. Current
FE U storage follows existing real-modal behavior; FE imaginary basis is zero.

For both models, use the same phase:

```text
theta = 2 * pi * frameIndex / sync.n_frames
position = origin + scale * (real * sin(theta) + imag * cos(theta))
```

No frame-coordinate arrays or full render buffers are returned. `include_frames`
is inherited for request compatibility but the new endpoint always returns basis
vectors. Existing amplitude normalization is reused, including cached full-model
displacement statistics; first access may still scan the full result for these
statistics. Selected FE vectors use HDF5 row selection, including sparse label
mapping, without building full-model deformation arrays or normals.

In wireframe mode, request geometry once and basis on mode changes. Do not request
full render buffers or full modal-animation frames. When the geometry version
changes, reload geometry and retry the basis request. Switching back to full
model mode uses the existing APIs. Actual production frontend integration must
be done in that frontend repository.
