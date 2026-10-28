# MAC comparison wireframe API

Existing full-mesh and animation endpoints remain available. These endpoints
provide an additional FE display model using experimental trace-line edges.
Display fallback matches never update saved node matches or MAC inputs.

The model-update BDF importer stores node matches under `BDF_MODEL`, while
the display pipeline uses the BDF filename as its instance name (for example,
`PROJECT_BDF_579DE83F`). When no exact instance exists and only one display
geometry instance is available, the wireframe resolves this known `BDF_MODEL`
alias to that instance. Returned `mapping.instance` uses the actual geometry
instance, also used to read modal displacements. These remain saved matches
(`source=matched`); they do not increment `nearest_count` or change database
rows. No alias is inferred when multiple geometry instances are available.

## Geometry

`POST /api/model/testMesh/femWireframe/geometry`

```json
{
  "project_id": 574,
  "order": 2,
  "step": "Modal",
  "fem_frame": 3,
  "result_group": "op2",
  "unmatched_policy": "disconnect",
  "coefficient": 1.0,
  "component": "usum",
  "animation": true
}
```

Both wireframe endpoints accept `index` as an alias for `order`. If both are
provided, `order` takes precedence. This is a modal order, not a zero-based frame:
`order=0` (default) means undeformed; positive orders default to FE frame
`order - 1`. `fem_frame` explicitly selects a zero-based FE frame, so a matched
pair of test mode 2 and FE mode 4 uses `order=2, fem_frame=3`. In the animation
endpoint, `order` also selects the experimental mode. No modal pairing is inferred.

Geometry accepts the display parameters of `testMesh/geometry`: `coefficient`,
`component`, `animation`, and `max_scalar_size`. As in the test endpoint, the
actual display size is derived from the returned geometry bounding box rather
than `max_scalar_size`. FE vectors keep their physical sign; `flip` applies only
to the experimental side of the synchronized animation endpoint.

`unmatched_policy`: `disconnect` (default) drops edges with unavailable endpoints;
`nearest` substitutes the nearest FE node without a distance cutoff. Distances
are returned so distant substitutions can be inspected. Search uses test
coordinates after the registration stored with the existing node matches.
With no saved matches, identity registration is used.

The standard response envelope contains `data` with:

- `ids`: experimental node IDs, in display vertex order.
- `originPos`: flat FE coordinates, three numbers per vertex.
- `elementsIndex`: flat zero-based line endpoint indices, two per edge.
- `newPos`: flat deformed FE coordinates (`originPos + real * scaleFactor`)
  for an available selected mode; empty for undeformed or unavailable modes.
- `scaleFactor`: deformation scale using the same bounding-box calculation as
  `testMesh/geometry`, based on the displayed FE nodes and their modal vectors.
- `componentData`, `minValue`, `maxValue`: unscaled modal amplitudes for the
  selected `component` (`usum`, `ux`, `uy`, or `uz`); zero for undeformed nodes.
- `real`, `imag`: flat selected FE basis when `animation=true`, otherwise empty.
  FE imaginary components are currently zero. These arrays and `newPos` use
  exactly the same vertex order as `originPos`.
- `missing_result_node_ids`: nodes without displacement results, held stationary.
- For a positive order, `available`, `reason`, `step`, `frame`, `result_group`,
  and `frequency` describe the selected FE result and any unavailability.
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
`missing_result_node_ids`. It also includes `originPos`, `elementsIndex`,
`newPos`, `componentData`, `minValue`, `maxValue`, and `scaleFactor` so its FE
payload can use the same rendering fields as `testMesh/geometry`.
`scaleFactor` equals the existing synchronized `scale`; `newPos` uses this scale.
This synchronized scale can differ from the standalone geometry scale, since
it normalizes both models together. When animating, use the scale returned by
the animation endpoint. FE basis order is exactly the geometry vertex order.
Missing node results remain stationary and are explicitly reported. Current
FE U storage follows existing real-modal behavior; FE imaginary basis is zero.

For both models, use the same phase:

```text
theta = 2 * pi * frameIndex / sync.n_frames
position = origin + scale * (real * sin(theta) + imag * cos(theta))
```

No per-frame coordinate arrays or full render buffers are returned. Only the
single real-basis deformation snapshot `newPos` is included. `include_frames`
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
