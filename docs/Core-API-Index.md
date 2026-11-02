# 核心版接口索引

此索引从本分支保留的路由源码静态提取；未启动服务，也不代表功能验证通过。完整请求参数见 `/openapi.json` 与源码。入口为 `app.py`。

## app.py

| 方法 | 路径 |
| --- | --- |
| POST | `/match/dofs` |
| POST | `/match/dofs/query` |
| POST | `/catalog/response/build` |
| POST | `/catalog/response` |
| POST | `/import/fem/modal` |
| POST | `/import/fem/modal/query` |
| POST | `/import/fem/static` |
| POST | `/import/fem/static/query` |
| POST | `/correlation/modal/compute` |
| POST | `/correlation/modal` |
| POST | `/correlation/modal/matrix` |
| POST | `/correlation/modal/table` |
| POST | `/correlation/modal/msf/table` |
| POST | `/correlation/modal/match/preview` |
| POST | `/correlation/modal/match` |
| POST | `/correlation/modal/match/frequency_scatter` |
| POST | `/correlation/static/compute` |

## webapi/routers/fem.py

| 方法 | 路径 |
| --- | --- |
| POST | `/import/bdf` |
| POST | `/import/inp/catalog` |
| POST | `/catalog/inp` |
| POST | `/catalog/inp/parameter-options` |
| POST | `/catalog/project_result/steps` |
| POST | `/tools/inp/tree` |
| POST | `/import/fem/static/from_project_result` |

## webapi/routers/matching.py

| 方法 | 路径 |
| --- | --- |
| POST | `/get/sensor_node_history` |
| POST | `/pair/node_point` |
| POST | `/match/nodes` |
| POST | `/get/pair_node_point_result` |
| POST | `/match/dofs` |
| POST | `/get/dof_match_result` |
| POST | `/match/nodes/params` |
| POST | `/correlation/evaluate` |
| POST | `/correlation/modal/match/frequency_scatter` |
| POST | `/correlation/modal/all_scatter` |
| POST | `/fem/modal/frequencies` |
| POST | `/correlation/modal/msf/table` |
| POST | `/correlation/modal/frequency_consistency` |
| POST | `/transform/operation` |
| POST | `/transform/auto_info` |

## webapi/routers/optimization.py

| 方法 | 路径 |
| --- | --- |
| POST | `/optimization/parameter/create` |
| POST | `/optimization/deck_solver/static_response/meta` |
| POST | `/optimization/deck_solver/static_response/node/validate` |
| POST | `/optimization/deck_solver/static_response/create` |
| POST | `/optimization/response/create` |
| POST | `/optimization/bulk_solver/modal_frequency_response/create` |
| POST | `/optimization/response/modal_frequency/create` |
| POST | `/optimization/bulk_solver/modal_frequency_response/create_from_match` |
| POST | `/optimization/response/modal_frequency/create_from_match` |
| POST | `/optimization/parameter` |
| POST | `/optimization/parameter/usage/update` |
| POST | `/optimization/parameter/select_for_update` |
| POST | `/optimization/parameter/remove_from_update` |
| POST | `/optimization/deck_solver/static_response` |
| POST | `/optimization/response` |
| POST | `/optimization/response/catalog` |
| POST | `/optimization/response/modal_match/select` |
| POST | `/optimization/response/modal_frequency/options` |
| POST | `/optimization/modal_mac/sensitivity/compute` |
| POST | `/optimization/deck_solver/static_response/clear` |
| POST | `/optimization/response/clear` |
| POST | `/optimization/sol200/parameter/create` |
| POST | `/optimization/sol200/parameter` |
| POST | `/optimization/sol200/parameter/clear` |
| POST | `/optimization/sol200/response/create` |
| POST | `/optimization/sol200/response` |
| POST | `/optimization/sol200/response/clear` |
| POST | `/optimization/sol200/config/sync_from_catalog` |
| POST | `/optimization/bayesian/run` |
| POST | `/optimization/bayesian/modal_frequency/run` |
| POST | `/optimization/bayesian/modal/run` |
| POST | `/optimization/bayesian/sol200/modal_frequency/run` |
| POST | `/optimization/bayesian/sol200/modal/run` |
| GET | `/optimization/bayesian/tasks` |
| POST | `/optimization/bayesian/check` |

## webapi/routers/sensitivity.py

| 方法 | 路径 |
| --- | --- |
| POST | `/sensitivity/deck_solver/calculate_and_store` |
| POST | `/sensitivity/calculate_and_store` |
| POST | `/sensitivity/deck_solver/run_and_store` |
| POST | `/sensitivity/run_and_store` |
| POST | `/sensitivity/deck_solver/generate_run_and_store` |
| POST | `/sensitivity/generate_run_and_store` |
| GET | `/sensitivity/tasks/{task_id}` |
| POST | `/sensitivity/dsa/config/preview` |
| POST | `/sensitivity/deck_solver/dsa/inp/generate` |
| POST | `/sensitivity/dsa/inp/generate` |
| POST | `/sensitivity/stored/table` |
| POST | `/sensitivity/stored/matrix` |
| POST | `/sensitivity/stored/curve/parameter` |
| POST | `/sensitivity/stored/curve/response` |
| POST | `/sensitivity/workspace/build` |
| POST | `/sensitivity/overview` |
| POST | `/sensitivity/table` |
| POST | `/sensitivity/export/vtu` |
| POST | `/sensitivity/export/vtu/dsa` |
| POST | `/sensitivity/export/vtu/adjoint` |
| POST | `/sensitivity/merge_fields` |

## webapi/routers/solver.py

| 方法 | 路径 |
| --- | --- |
| POST | `/solver/deck_solver/sensitivity` |
| POST | `/solver/deck_solver/adjoint` |
| POST | `/solver/bulk_solver/sol103` |
| POST | `/solver/bulk_solver/sol103/preview` |
| POST | `/solver/bulk_solver/sol103/generate` |
| POST | `/solver/bulk_solver/sol103/run` |
| POST | `/solver/bulk_solver/sol103/run_and_store_modal` |
| POST | `/solver/run_and_parse` |
| GET | `/solver/bulk_solver/tasks` |
| POST | `/solver/bulk_solver/sol200/preview` |
| POST | `/solver/bulk_solver/sol200/generate` |
| POST | `/solver/bulk_solver/sol200/run` |
| POST | `/solver/bulk_solver/sol200/run_and_store` |
| POST | `/solver/bulk_solver/sol200/sync_generate_run_and_store` |
| POST | `/solver/bulk_solver/sol200/modal/run_and_store` |
| POST | `/solver/bulk_solver/sol200/modal_mac/run_and_store` |
| POST | `/import/op2/modal/preview` |
| POST | `/import/op2/modal/store` |
| POST | `/export/op2/modal/vtu` |
| POST | `/import/op2/sensitivity/preview` |
| POST | `/import/op2/sensitivity/store` |
| POST | `/import/op2/sensitivity/store_cloud` |
| POST | `/export/op2/sensitivity/vtu` |

## webapi/routers/system.py

| 方法 | 路径 |
| --- | --- |
| POST | `/init` |
| POST | `/project/config` |
| POST | `/project/config/query` |
| GET | `/calc/tasks/{task_id}` |
| GET | `/calc/task/status` |

## webapi/routers/test_data.py

| 方法 | 路径 |
| --- | --- |
| POST | `/import/test_load_level` |
| POST | `/get/sensor_load_level_curve` |
| POST | `/import/unv` |
| POST | `/plot/modal_shape` |
| POST | `/get/sensor_position` |
| POST | `/get/sensor_relative_error` |
| POST | `/get/deform_sensor_position` |
| POST | `/test/unit/convert` |
| POST | `/test/unit/status` |

## src/l3/api/routes/color_code.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/color-code/{instance}/display-names` |
| GET | `/api/odb/{odb_id}/color-code/legend-entries` |
| GET | `/api/odb/{odb_id}/color-code/{instance}/legend-entries` |
| POST | `/api/odb/{odb_id}/color-code/{instance}/legend-entries` |
| GET | `/api/odb/{odb_id}/color-code/{instance}/legend` |
| POST | `/api/odb/{odb_id}/color-code/{instance}/display-names` |
| GET | `/api/odb/{odb_id}/color-code/{instance}/schemes` |
| GET | `/api/odb/{odb_id}/color-code/{instance}` |
| GET | `/api/odb/{odb_id}/color-code/{instance}/region-mesh-edges` |
| GET | `/api/odb/{odb_id}/color-code/{instance}/region-outline` |

## src/l3/api/routes/external_results.py

| 方法 | 路径 |
| --- | --- |
| POST | `/api/odb/{odb_id}/results/external-field` |

## src/l3/api/routes/geometry.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/geometry/orientations` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/node-labels` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/render-buffers` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/render-buffers-chunked` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/element-mesh-edges` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/feature-edges` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/lines` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/points` |
| GET | `/api/odb/{odb_id}/geometry/{instance}/couplings` |
| POST | `/api/odb/{odb_id}/geometry/{instance}/render-buffers-subset` |
| POST | `/api/odb/{odb_id}/geometry/{instance}/render-buffers-subset-chunked` |

## src/l3/api/routes/health.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/health/live` |
| GET | `/api/health/ready` |

## src/l3/api/routes/jobs.py

| 方法 | 路径 |
| --- | --- |
| POST | `/api/jobs` |
| GET | `/api/jobs` |
| GET | `/api/jobs/{odb_id}` |
| DELETE | `/api/jobs/{odb_id}` |
| GET | `/api/jobs/{odb_id}/logs` |
| POST | `/api/jobs/{odb_id}/retry` |

## src/l3/api/routes/meta.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/meta/overview` |
| GET | `/api/odb/{odb_id}/steps` |
| GET | `/api/odb/{odb_id}/steps/{step_name}/frames` |
| GET | `/api/odb/{odb_id}/steps/{step_name}/frames/{frame_idx}` |

## src/l3/api/routes/modal.py

| 方法 | 路径 |
| --- | --- |
| POST | `/api/modal/load` |
| GET | `/api/modal/{model_id}/geometry` |
| GET | `/api/modal/{model_id}/modes` |
| GET | `/api/modal/{model_id}/components` |
| GET | `/api/modal/{model_id}/deformed` |
| GET | `/api/modal/{model_id}/animation` |
| GET | `/api/modal/{model_id}/colormap` |

## src/l3/api/routes/model_update.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/model-update/result_groups` |
| GET | `/api/odb/{odb_id}/model-update/step_frame` |
| GET | `/api/odb/{odb_id}/model-update/fields` |

## src/l3/api/routes/node_table.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/fields` |
| POST | `/api/odb/{odb_id}/results/node-table` |
| POST | `/api/odb/{odb_id}/results/node-time-value` |

## src/l3/api/routes/projects.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/projects` |
| POST | `/api/projects` |
| POST | `/api/projects/{source_project_id}/clone` |
| POST | `/api/projects/{project_id}/results` |
| POST | `/api/projects/{project_id}/results/{result_group}/reparse` |
| GET | `/api/projects/{project_id}/logs` |
| POST | `/api/projects/{project_id}/logs/clear` |
| GET | `/api/projects/{project_id}` |
| GET | `/api/projects/{project_id}/result-catalog` |
| PATCH | `/api/projects/{project_id}/results/{result_group}` |
| GET | `/api/projects/{project_id}/summary` |
| POST | `/api/projects/{project_id}/rerun-l2` |
| DELETE | `/api/projects/{project_id}` |

## src/l3/api/routes/query.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/query/pick` |
| POST | `/api/odb/{odb_id}/query/ray-pick` |
| POST | `/api/odb/{odb_id}/query/bbox` |
| POST | `/api/odb/{odb_id}/query/render-faces` |
| POST | `/api/odb/{odb_id}/query/surface-patch` |
| GET | `/api/odb/{odb_id}/query/nearest-face` |
| POST | `/api/odb/{odb_id}/query/node-displacements` |

## src/l3/api/routes/raw_results.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/results/raw-values` |

## src/l3/api/routes/results.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/results/frame-colors` |
| GET | `/api/odb/{odb_id}/results/frame-scalars` |
| POST | `/api/odb/{odb_id}/results/warmup` |
| GET | `/api/odb/{odb_id}/results/frame-scalar-range` |
| POST | `/api/odb/{odb_id}/results/frame-scalar-range` |
| GET | `/api/odb/{odb_id}/results/deformed-positions` |
| GET | `/api/odb/{odb_id}/results/vertex-displacements` |
| GET | `/api/odb/{odb_id}/results/deformed-normals` |
| GET | `/api/odb/{odb_id}/results/modal-shape` |
| GET | `/api/odb/{odb_id}/results/modal-animation` |
| GET | `/api/odb/{odb_id}/results/deform-suggest-scale` |

## src/l3/api/routes/section.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/results/section-mesh` |

## src/l3/api/routes/sensitivity.py

| 方法 | 路径 |
| --- | --- |
| GET | `/api/odb/{odb_id}/sensitivity/result_groups` |
| GET | `/api/odb/{odb_id}/sensitivity/step_frame` |
| GET | `/api/odb/{odb_id}/sensitivity/fields` |

## src/l3/api/routes/user_field.py

| 方法 | 路径 |
| --- | --- |
| POST | `/api/odb/{odb_id}/results/user-field` |
| GET | `/api/odb/{odb_id}/results/user-fields` |
| GET | `/api/odb/{odb_id}/results/user-field-colors` |
| DELETE | `/api/odb/{odb_id}/results/user-field` |

## src/modal_service/routes.py

| 方法 | 路径 |
| --- | --- |
| POST | `/api/model/testMesh/femWireframe/geometry` |
| POST | `/api/model/testMesh/femWireframe/animation` |
| POST | `/api/model/testMesh/geometry` |
| POST | `/api/model/testMesh/modelSelect` |
| POST | `/api/model/testMesh/animation` |
| POST | `/api/model/testMesh/syncAnimation` |
| POST | `/api/model/testMesh/colormap` |
