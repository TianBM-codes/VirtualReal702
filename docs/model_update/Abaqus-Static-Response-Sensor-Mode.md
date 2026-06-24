# Abaqus Static Response Sensor Mode

## Purpose

This note supplements the existing interface docs for:

- `POST /optimization/abaqus/static_response/create`
- `POST /optimization/response/create`

It documents the newly supported "sensor mode" for Abaqus static-response creation.

## Two Input Modes

The response-create interface now supports two mutually exclusive modes.

### 1. FEM node mode

Use this when the caller already knows the exact FEM node.

Required/typical fields:

- `project_id`
- `region_type`
- `variables`
- `step_name`
- `instance_name`
- `node_labels`

Example:

```json
{
  "project_id": 5,
  "region_type": "NODE",
  "instance_name": "PART-1-1",
  "node_labels": [4],
  "variables": ["U2"],
  "step_name": "Step-1",
  "frequency": 1
}
```

### 2. Sensor mode

Use this when the caller starts from a test sensor / measuring point instead of a FEM node.

Required/typical fields:

- `project_id`
- `region_type`
- `sensor_name`
- `variables`
- `step_name`

Example:

```json
{
  "project_id": 5,
  "region_type": "NODE",
  "sensor_name": "WY1",
  "variables": ["U2"],
  "step_name": "Step-1",
  "frequency": 1
}
```

## Resolution Rule

In sensor mode, the backend resolves the FEM node from:

- table: `t_mt_py_fem_node_match`
- lookup key: `pid + test_node_id`

The backend reads:

- `instance_name`
- `fem_node_label`

and then creates a normal Abaqus node response from that resolved FEM node.

## Constraints

Sensor mode currently supports only:

- `region_type=NODE`
- `variables` in `U1 / U2 / U3`
- single response creation
- mandatory `step_name`

Sensor mode must not be combined with:

- `set_name`
- `instance_name`
- `node_labels`
- `element_labels`

If `sensor_name` is not found in `t_mt_py_fem_node_match`, the interface returns an error directly.

## Step Rule

`step_name` is required for sensor mode.

More generally, the system should not default to "the last step".
Only when all resolved responses point to the same step may downstream logic inherit that single step automatically.

## Returned Data

The response is still stored as a normal Abaqus static response entry.
In sensor mode, the returned `data` also includes the input `sensor_name` for traceability.

Example:

```json
{
  "project_id": 5,
  "response_no": 2,
  "request_no": 1,
  "response_name": "RESP_2",
  "step_name": "Step-1",
  "frequency": 1,
  "region_type": "NODE",
  "set_name": "MANUAL_RESP_NODE_RESP_2_2",
  "set_scope": "ASSEMBLY",
  "instance_name": "PART-1-1",
  "part_name": "PART-1",
  "variables": ["U2"],
  "node_labels": [4],
  "sensor_name": "WY1"
}
```
