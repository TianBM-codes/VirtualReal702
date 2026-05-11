# Nastran SOL200 Phase 1 Plan

## 1. Goal

The first phase focuses on one narrow but usable SOL200 workflow:

- analysis type: modal sensitivity
- responses: frequency only
- parameters: `E`, `RHO`, `H`
- parameter relations: `DVMREL1(MAT1)` and `DVPREL1(PSHELL)`
- outputs:
  - generated SOL200 BDF
  - solver artifacts
  - sensitivity matrix preview
  - sensitivity matrix storage
  - VTU export for element sensitivity fields

This phase is intentionally smaller than the full SOL200 capability set shown in
`temp/web` and the historical FEMTools BAS scripts.

## 2. Main References

### BAS references

- `femtools_bas/nastran.bas`
- `femtools_bas/nastranopt.bas`

These scripts are used as business-logic references, especially for:

- `DESVAR`
- `DVMREL1`
- `DVPREL1`
- `DRESP1`
- `DCONSTR`
- `DESSUB`
- `DSAPRT`

They are **not** treated as ready-to-run templates. The Python service owns the
final deck assembly.

### Example BDF references

Priority order for phase-1 learning:

1. `temp/web/solution_ws_dsoug2`
2. `temp/web/solution_ws_dsoug1_sensitivity_analysis`
3. `temp/web/solution_ws_dsoug1`

These are the closest references for the first modal-frequency sensitivity
workflow.

## 3. Phase-1 Scope

### Included

- Dedicated orchestration service for SOL200 workflows
- Include-style deck generation:
  - main analysis deck
  - separate `design_model.bdf`
- Preview/generate/run endpoints for SOL200
- Force mass-normalized modal extraction for sensitivity decks
- Convenience preset for automatically expanding all used `MAT1` material `E`
  and `RHO` values into design parameters
- Basic sensitivity matrix import from:
  - OP2
  - explicit matrix result file path
- Database storage through the existing sensitivity tables
- VTU export for stored sensitivity results

### Excluded for now

- `DRESP2`
- `DEQATN`
- `DLINK`
- `DDVAL`
- `DVPREL2` / `DVMREL2`
- multiple subcases
- multidisciplinary examples
- buckling/failure/composite optimization cases
- exact reproduction of every FEMTools internal optimization workflow

## 4. Architecture

Phase 1 uses a layered structure:

### Card generation layer

- `services/model_update/solver_prep/nastran_sol200.py`

Responsibilities:

- assemble a complete phase-1 SOL200 deck
- normalize settings for modal sensitivity
- keep the supported card set small and explicit
- use a safer free-field style for generated design cards so long numeric values
  do not break fixed-column parsing

### Solver orchestration layer

- `services/model_update/analysis/nastran_sol200_service.py`

Responsibilities:

- expose a single service entrypoint for all phase-1 SOL200 workflows
- keep SOL200 logic separate from generic solver helpers
- shield routers from low-level SOL200 details

### Result parsing layer

- `services/model_update/importers/op2_service.py`

Responsibilities:

- preview sensitivity matrices
- import sensitivity matrices into database payloads
- export stored sensitivity results to VTU

## 5. Supported Interfaces in Phase 1

- `POST /solver/nastran/sol200/preview`
- `POST /solver/nastran/sol200/generate`
- `POST /solver/nastran/sol200/run`
- `POST /import/op2/sensitivity/preview`
- `POST /import/op2/sensitivity/store`
- `POST /export/op2/sensitivity/vtu`

## 6. Acceptance Criteria

Phase 1 is considered complete when all conditions below are satisfied:

1. A valid phase-1 SOL200 deck can be generated from one BDF and a parameter /
   response payload.
2. The deck clearly contains:
   - `SOL 200`
   - `METHOD`
   - `EIGRL`
   - `DESSUB`
   - `DSAPRT`
   - `DESVAR`
   - `DVMREL1` or `DVPREL1`
   - `DRESP1`
   - `DCONSTR`
3. The solver run API returns a complete artifact summary so the caller can see
   whether the useful result is in `op2`, `unit11`, or another matrix file.
4. The sensitivity preview API can show a matrix with row/column labels, even if
   some labels are heuristic.
5. The sensitivity store API can write the matrix into the existing database
   structure.
6. The VTU export API can write one response row as an element field.

## 7. Planned Phase-2 Extensions

After phase 1 is stable, the next expansion order should be:

1. `DCONADD` and richer response grouping
2. `DRESP2`
3. `DEQATN`
4. `DLINK`
5. `DDVAL`
6. `DVPREL2`
7. multi-subcase and model-matching examples

## 8. Notes

- The current project goal is to build a reliable backend capability, not to
  perfectly clone every hidden FEMTools internal optimization step.
- For the user-facing workflow, what matters first is:
  - the deck is valid
  - the matrix can be found
  - the labels and storage are auditable
  - the result can be visualized and compared
