# VirtualReal702 Project Context

## Goal

This project is used for model updating around Abaqus and test data.

Current high-level workflow:

1. Import FE-side model data.
2. Import test-side data.
3. Match test points to FE nodes and later match DOFs.
4. Compute confidence factors such as DAC / DSF.
5. Extract candidate parameters and sets from a normal `inp` file so users can create optimization parameters.
6. Later, external tooling will modify `inp`, run Abaqus, generate `odb`, and extract results.

## Current Decisions

### Sensitivity

- Sensitivity persistence to MySQL is postponed.
- The first agreed aggregation rule for sensitivity is `max_abs`.
- The real frontend need is table display, sorting, and matrix access for charting.
- Sensitivity should stay decoupled from the current import / match work.

### FE Parameter Strategy

- The input `inp` is a normal Abaqus `inp`, not an optimization-specific one.
- Therefore the system does not try to identify "official optimization parameters" directly.
- Instead, it extracts candidate scalar values that are stable and easy to write back later.
- Users will choose:
  - one candidate parameter
  - one set
  - then create a user-facing optimization parameter

### Set Strategy

- `NSET` and `ELSET` from both Part scope and Assembly scope are stored in database.
- They are used as selectable catalogs for later optimization parameter creation.

### Node Matching Strategy

- Node matching should use a node octree, not the existing L2 surface-face octree.
- The node octree is built from FE global node coordinates extracted from the parsed `inp`.
- The cache is saved locally under `model_cache/project_<project_id>/`.

## Implemented In This Round

### New Database Tables

Added in `db.py`:

- `t_mt_py_fem_parameter_candidate`
- `t_mt_py_fem_set_catalog`
- `t_mt_py_fem_optimization_parameter`
- `t_mt_py_fem_node_octree_cache`
- `t_mt_py_fem_node_match`
- `t_mt_py_fem_dof_match`
- `t_mt_py_fem_response_catalog`
- `t_mt_py_fem_modal_result`
- `t_mt_py_fem_modal_correlation`

Also extended cleanup logic:

- `clear_fem_tables()` now clears these derived FE-side records
- `clear_unv_tables()` also clears downstream match / correlation records that depend on imported test data

### New Service

Added `services/inp_service.py`.

It currently provides:

- `import_inp_catalog()`
  - parse `inp`
  - extract candidate parameters
  - extract set catalog
  - build and cache node octree
  - persist metadata to MySQL
- `get_inp_catalog()`
  - read back candidate parameters, sets, optimization parameters, octree metadata
- `create_optimization_parameter()`
  - create a user-defined optimization parameter from one candidate and one set
- `match_test_nodes()`
  - load cached node octree
  - read imported test nodes from `t_mt_py_test_node`
  - support manual translation / rigid rotation
  - support automatic rigid transform estimation with translation + rotation
  - find nearest FE node
  - store results in `t_mt_py_fem_node_match`
- `match_test_dofs()`
  - read node matches
  - map test translational DOFs (`UX/UY/UZ`) to FE nodal DOFs (`U1/U2/U3`)
  - project test directions through the estimated rigid transform
  - store results in `t_mt_py_fem_dof_match`
- `build_fe_response_catalog()`
  - build model-updating response catalog from:
    - test modal frequencies
    - matched node-DOF responses
  - store results in `t_mt_py_fem_response_catalog`
- `import_fe_modal_results()`
  - import FE modal nodal displacement results from JSON file or request payload
  - persist them in `t_mt_py_fem_modal_result`
- `compute_modal_correlation()`
  - read test modal shapes
  - read DOF matches
  - read imported FE modal results
  - compute DAC / DSF for each test mode vs FE mode pair
  - persist pairwise results in `t_mt_py_fem_modal_correlation`
  - also update legacy compatibility table `t_mt_py_fem_static_shape_pairs`
- query helpers:
  - `get_dof_matches()`
  - `get_fe_response_catalog()`
  - `get_fe_modal_results()`
  - `get_modal_correlation()`

### New POST APIs

Added in `app.py`:

- `POST /import/bdf`
- `POST /import/inp/catalog`
- `POST /catalog/inp`
- `POST /optimization/parameter/create`
- `POST /optimization/parameter`
- `POST /optimization/response/modal_frequency/create_from_match`
- `POST /optimization/sol200/config/sync_from_catalog`
- `POST /match/nodes`
- `POST /match/dofs`
- `POST /match/dofs/query`
- `POST /catalog/response/build`
- `POST /catalog/response`
- `POST /import/fem/modal`
- `POST /import/fem/modal/query`
- `POST /correlation/modal/compute`
- `POST /correlation/modal`

## Current Project Status

### Stable Now

- Normal `inp` import and catalog extraction is implemented.
- FE node octree build / cache / reload is implemented.
- Test-node to FE-node matching is implemented.
- Automatic rigid transform estimation now supports rotation as well as translation.
- Test DOF to FE DOF matching is implemented for translational DOFs.
- Response catalog generation for model-updating selection is implemented.
- FE modal result import is implemented through a backend API.
- DAC / DSF modal correlation computation is implemented once:
  - test modal data is already imported
  - node matching is done
  - DOF matching is done
  - FE modal results are imported

### Still Not Implemented

- automatic rotation detection specialized for more difficult partial-overlap / noisy cases beyond the current generic rigid-fit approach
- rotational DOF matching (`RX/RY/RZ`)
- direct FE modal extraction from `odb`
- FE response catalog extraction directly from Abaqus result files
- mode-pair recommendation / auto-pairing policy beyond raw DAC sorting
- `inp` writing-back for created optimization parameters
- full optimization run orchestration (`inp` modify -> Abaqus run -> `odb` extract)

## API Summary

### `POST /import/inp/catalog`

Purpose:

- import one normal `inp`
- extract candidate parameters and sets
- build node octree cache

Request body:

```json
{
  "file_path": "D:/models/model.inp",
  "project_id": 1001,
  "clear_before_insert": true,
  "build_octree": true,
  "force_rebuild_octree": false
}
```

### `POST /catalog/inp`

Purpose:

- query imported candidate parameters, sets, optimization parameters, octree cache metadata

Request body:

```json
{
  "project_id": 1001
}
```

### `POST /optimization/parameter/create`

Purpose:

- create one optimization parameter from one candidate parameter plus one set

Request body:

```json
{
  "project_id": 1001,
  "candidate_code": "MAT:STEEL:E",
  "set_name": "SET-1",
  "parameter_name": "E_on_SET_1",
  "description": "Young modulus on set 1",
  "set_type": "ELSET",
  "set_scope": "PART"
}
```

### `POST /match/nodes`

Purpose:

- match imported test points to FE nodes using cached node octree
- optionally estimate rigid transform automatically
- persist the transform payload for downstream DOF matching

Request body:

```json
{
  "project_id": 1001,
  "max_distance": 5.0,
  "overwrite": true,
  "auto_translate": true,
  "auto_rotate": true,
  "translation": [0.0, 0.0, 0.0],
  "rotation": {
    "center": [0.0, 0.0, 0.0],
    "axis": [0.0, 0.0, 1.0],
    "angle_deg": 0.0
  }
}
```

### `POST /match/dofs`

Purpose:

- build translational DOF mapping from matched test points to FE nodal displacement components

Request body:

```json
{
  "project_id": 1001,
  "overwrite": true
}
```

### `POST /catalog/response/build`

Purpose:

- build project-level response catalog for model updating
- include modal-frequency responses and matched nodal displacement responses

Request body:

```json
{
  "project_id": 1001,
  "overwrite": true,
  "include_test_modes": true,
  "include_node_dofs": true
}
```

### `POST /catalog/response`

Purpose:

- query built FE response catalog

Request body:

```json
{
  "project_id": 1001
}
```

### `POST /import/fem/modal`

Purpose:

- import FE modal nodal displacement results used for DAC / DSF comparison

Request body example using JSON file:

```json
{
  "project_id": 1001,
  "overwrite": true,
  "file_path": "D:/results/fem_modal_results.json"
}
```

Request body example using inline payload:

```json
{
  "project_id": 1001,
  "overwrite": true,
  "modes": [
    {
      "mode_no": 1,
      "frequency": 123.4,
      "nodes": [
        {
          "instance_name": "PART-1-1",
          "fem_node_label": 10001,
          "vector": [0.12, 0.01, -0.03]
        }
      ]
    }
  ]
}
```

### `POST /correlation/modal/compute`

Purpose:

- compute DAC / DSF between each test mode and each imported FE mode using matched DOFs
- optionally filter out low-MAC combinations before they are written to `t_mt_py_fem_modal_correlation`

Request body:

```json
{
  "project_id": 1001,
  "overwrite": true,
  "mac_threshold": 80
}
```

### `POST /optimization/parameter`

Purpose:

- query saved optimization parameters from `t_mt_py_fem_selected_parameter`

Request body:

```json
{
  "project_id": 1001
}
```

### `POST /optimization/response/modal_frequency/create_from_match`

Purpose:

- build modal-frequency response catalog rows from the already computed modal-match result

Request body:

```json
{
  "project_id": 1001,
  "overwrite": true,
  "mac_threshold": 80,
  "max_freq_error_ratio": 0.15,
  "matching_method": "greedy"
}
```

### `POST /optimization/sol200/config/sync_from_catalog`

Purpose:

- map saved optimization parameters plus modal-frequency response catalog rows into `SOL200` parameter/response config tables

Request body:

```json
{
  "project_id": 1001,
  "overwrite": true,
  "parameter_source": "selected_parameter",
  "response_source": "response_catalog"
}
```

Notes:

- `mac_threshold` is optional.
- `mac_threshold` uses the same percentage scale as stored `MAC` values in this interface, so valid values are `0` to `100`.
- Example: use `80` to keep only pairs with `MAC >= 80`.

### `POST /correlation/modal`

Purpose:

- query computed modal correlation results

Request body:

```json
{
  "project_id": 1001
}
```

## Current Extraction Scope

Candidate parameter extraction currently covers:

- `*DENSITY`
- `*ELASTIC`
- `*SHELL SECTION` thickness
- `*MEMBRANE SECTION` thickness
- `*BEAM SECTION` dimensions

This is intentionally the first stable subset.

## Current Matching Scope

The current matching / correlation chain is now version 2.

Implemented:

- nearest-node lookup through node octree
- optional manual rigid transform
- automatic rigid transform estimation with translation + rotation
- translational DOF matching (`UX/UY/UZ` -> `U1/U2/U3`)
- response catalog generation for modal updating
- DAC / DSF calculation for test-mode vs FE-mode pairs after FE modal result import

Not implemented yet:

- rotational DOF matching
- direct FE modal extraction from Abaqus result files
- automatic recommended mode pairing policy
- writing back `inp`

## Suggested Next Steps

1. Add FE modal result extraction directly from `odb`, so DAC / DSF no longer depends on manual JSON import.
2. Extend DOF matching to rotational DOFs and richer test coordinate systems.
3. Add better mode-pair recommendation logic based on DAC + frequency error + optional user constraints.
4. Connect created optimization parameters to future `inp` write-back logic.
5. Revisit sensitivity matrix service after the model-updating data preparation chain is stable.
