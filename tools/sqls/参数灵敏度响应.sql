SELECT * FROM `t_mt_py_fem_node_match` where pid = 3;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_dof_match` where pid=3 LIMIT 0,1000;
SELECT distinct(frequency) FROM `db_simu_real_test`.`t_mt_py_fem_modal_result` where pid=3 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_modal_correlation` where pid=3 LIMIT 0,1000;

SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_parameter_def` where project_id=3 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_parameter_definition` where pid=3 LIMIT 0,1000;

SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_property` where pid=3 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_quantity_set_capability` where pid=3 LIMIT 0,1000;

SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_selected_parameter` where pid=3 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_sol200_response_config` LIMIT 0,1000;

SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_response_catalog` where pid=3 LIMIT 0,1000;

SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_sol200_parameter_config` where pid=3 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_sol200_response_config` where pid=3 LIMIT 0,1000;


# 创建sol200bdf文件
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_quantity_set_capability` where pid=4 and quantity_code='E' LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_modal_result` where pid=4 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_selected_parameter` where pid=4 LIMIT 0,1000;
SELECT * FROM `db_simu_real_test`.`t_mt_py_fem_sol200_parameter_config` where pid=4 LIMIT 0,1000;
DELETE FROM `db_simu_real_test`.`t_mt_py_fem_sol200_parameter_config` WHERE `pid` = 4;
