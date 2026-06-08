"""Facade for INP/FEM workflows.

This module keeps the historical import path stable while delegating
catalog/parameter, matching, response, result, and correlation
responsibilities into smaller modules.
"""

from . import fem_catalog_service as _catalog
from . import fem_matching_service as _matching
from . import fem_response_service as _response
from . import fem_result_service as _result
from . import fem_correlation_service as _correlation
from .fem_catalog_service import *
from .fem_matching_service import *
from .fem_response_service import *
from .fem_result_service import *
from .fem_correlation_service import *

_ORIGINAL_RESULT_REGISTRY_REPO = _result._registry_repo


def _sync_catalog_dependencies():
    _catalog.ensure_tables_exist = ensure_tables_exist
    _catalog.get_connection = get_connection
    _catalog.parse_inp = parse_inp
    _catalog.safe_write_console_event = safe_write_console_event
    _catalog.log_project_error = log_project_error
    _catalog.log_project_info = log_project_info
    _catalog.log_project_step = log_project_step
    _catalog.get_test_data_mode = get_test_data_mode
    _catalog.get_node_match_parameter_context = get_node_match_parameter_context
    _catalog.save_fem_model_dimensions = save_fem_model_dimensions
    _catalog.resolve_project_cal_subdir = resolve_project_cal_subdir
    _catalog.resolve_project_source_inp_path = resolve_project_source_inp_path
    _catalog.update_work_condition_project_status = update_work_condition_project_status
    _catalog.settings = settings
    _catalog._sens = _sens
    _catalog._parse_id_list = _parse_id_list
    _catalog._parse_optional_json_object = _parse_optional_json_object
    return _catalog


def _sync_matching_dependencies():
    _matching.ensure_tables_exist = ensure_tables_exist
    _matching.get_connection = get_connection
    _matching.parse_inp = parse_inp
    _matching.get_test_data_mode = get_test_data_mode
    _matching.resolve_project_cal_subdir = resolve_project_cal_subdir
    _matching.save_fem_model_dimensions = save_fem_model_dimensions
    _matching._save_octree_cache = _save_octree_cache
    _matching._load_octree_cache = _load_octree_cache
    _matching._cache_part_lookup = _cache_part_lookup
    _matching._required_operation_error = _required_operation_error
    return _matching


def _sync_response_dependencies():
    _sync_matching_dependencies()
    _response.ensure_tables_exist = ensure_tables_exist
    _response.get_connection = get_connection
    _response.safe_write_console_event = safe_write_console_event
    return _response


def _sync_result_dependencies():
    _sync_response_dependencies()
    _result.ensure_tables_exist = ensure_tables_exist
    _result.get_connection = get_connection
    _result.log_project_step = log_project_step
    _result.log_project_info = log_project_info
    _result.log_project_error = log_project_error
    facade_registry_repo = globals().get("_registry_repo")
    if facade_registry_repo is _registry_repo:
        _result._registry_repo = _ORIGINAL_RESULT_REGISTRY_REPO
    else:
        _result._registry_repo = facade_registry_repo
    _result._sens = _sens
    return _result


def _sync_correlation_dependencies():
    _sync_result_dependencies()
    _correlation.ensure_tables_exist = ensure_tables_exist
    _correlation.get_connection = get_connection
    _correlation.get_test_data_mode = get_test_data_mode
    _correlation.safe_write_console_event = safe_write_console_event
    _correlation.log_project_step = log_project_step
    _correlation.log_project_info = log_project_info
    _correlation.log_project_error = log_project_error
    _correlation._require_modal_project = _require_modal_project
    _correlation._require_non_modal_project = _require_non_modal_project
    _correlation._ensure_node_matches = _ensure_node_matches
    _correlation._ensure_static_node_matches = _ensure_static_node_matches
    _correlation._resolve_modal_mac_mode = _resolve_modal_mac_mode
    _correlation._load_test_modal_frequencies = _load_test_modal_frequencies
    _correlation._compute_dac_dsf = _compute_dac_dsf
    return _correlation


def import_inp_catalog(*args, **kwargs):
    return _sync_catalog_dependencies().import_inp_catalog(*args, **kwargs)


def get_inp_catalog(*args, **kwargs):
    return _sync_catalog_dependencies().get_inp_catalog(*args, **kwargs)


def get_inp_parameter_options(*args, **kwargs):
    return _sync_catalog_dependencies().get_inp_parameter_options(*args, **kwargs)


def create_optimization_parameter(*args, **kwargs):
    return _sync_catalog_dependencies().create_optimization_parameter(*args, **kwargs)


def create_design_response_catalog_entry(*args, **kwargs):
    return _sync_catalog_dependencies().create_design_response_catalog_entry(*args, **kwargs)


def list_design_response_catalog_entries(*args, **kwargs):
    return _sync_catalog_dependencies().list_design_response_catalog_entries(*args, **kwargs)


def clear_design_response_catalog_entries(*args, **kwargs):
    return _sync_catalog_dependencies().clear_design_response_catalog_entries(*args, **kwargs)


def list_optimization_parameters(*args, **kwargs):
    return _sync_catalog_dependencies().list_optimization_parameters(*args, **kwargs)


def update_optimization_parameter_usage(*args, **kwargs):
    return _sync_catalog_dependencies().update_optimization_parameter_usage(*args, **kwargs)


def select_optimization_parameters_for_update(*args, **kwargs):
    return _sync_catalog_dependencies().select_optimization_parameters_for_update(*args, **kwargs)


def remove_optimization_parameters_from_update(*args, **kwargs):
    return _sync_catalog_dependencies().remove_optimization_parameters_from_update(*args, **kwargs)


def _get_latest_octree_meta(*args, **kwargs):
    return _sync_matching_dependencies()._get_latest_octree_meta(*args, **kwargs)


def _ensure_octree_cache_file(*args, **kwargs):
    return _sync_matching_dependencies()._ensure_octree_cache_file(*args, **kwargs)


def match_test_nodes(*args, **kwargs):
    return _sync_matching_dependencies().match_test_nodes(*args, **kwargs)


def get_pair_node_point_result(*args, **kwargs):
    return _sync_matching_dependencies().get_pair_node_point_result(*args, **kwargs)


def save_transform_operation(*args, **kwargs):
    return _sync_matching_dependencies().save_transform_operation(*args, **kwargs)


def get_transform_auto_info(*args, **kwargs):
    return _sync_matching_dependencies().get_transform_auto_info(*args, **kwargs)


def match_test_dofs(*args, **kwargs):
    return _sync_matching_dependencies().match_test_dofs(*args, **kwargs)


def get_dof_matches(*args, **kwargs):
    return _sync_matching_dependencies().get_dof_matches(*args, **kwargs)


def _ensure_node_matches(*args, **kwargs):
    return _sync_matching_dependencies()._ensure_node_matches(*args, **kwargs)


def _ensure_static_node_matches(*args, **kwargs):
    return _sync_matching_dependencies()._ensure_static_node_matches(*args, **kwargs)


def build_fe_response_catalog(*args, **kwargs):
    return _sync_response_dependencies().build_fe_response_catalog(*args, **kwargs)


def get_fe_response_catalog(*args, **kwargs):
    return _sync_response_dependencies().get_fe_response_catalog(*args, **kwargs)


def get_modal_frequency_response_options(*args, **kwargs):
    return _sync_response_dependencies().get_modal_frequency_response_options(*args, **kwargs)


def create_modal_frequency_response_catalog_from_match(*args, **kwargs):
    return _sync_response_dependencies().create_modal_frequency_response_catalog_from_match(*args, **kwargs)


def create_modal_match_response_catalog_entries(*args, **kwargs):
    return _sync_response_dependencies().create_modal_match_response_catalog_entries(*args, **kwargs)


def _load_modal_payload(*args, **kwargs):
    return _sync_result_dependencies()._load_modal_payload(*args, **kwargs)


def _resolve_modal_node_identity(*args, **kwargs):
    return _sync_result_dependencies()._resolve_modal_node_identity(*args, **kwargs)


def import_fe_modal_results(*args, **kwargs):
    return _sync_result_dependencies().import_fe_modal_results(*args, **kwargs)


def get_fe_modal_results(*args, **kwargs):
    return _sync_result_dependencies().get_fe_modal_results(*args, **kwargs)


def _manifest_result_group_clause(*args, **kwargs):
    return _sync_result_dependencies()._manifest_result_group_clause(*args, **kwargs)


def _registry_repo(*args, **kwargs):
    return _sync_result_dependencies()._registry_repo(*args, **kwargs)


def _resolve_project_result_workspace(*args, **kwargs):
    return _sync_result_dependencies()._resolve_project_result_workspace(*args, **kwargs)


def _collect_project_result_static_rows(*args, **kwargs):
    return _sync_result_dependencies()._collect_project_result_static_rows(*args, **kwargs)


def _load_static_result_rows_from_txt(*args, **kwargs):
    return _sync_result_dependencies()._load_static_result_rows_from_txt(*args, **kwargs)


def _load_static_result_payload(*args, **kwargs):
    return _sync_result_dependencies()._load_static_result_payload(*args, **kwargs)


def _persist_fe_static_results(*args, **kwargs):
    return _sync_result_dependencies()._persist_fe_static_results(*args, **kwargs)


def import_fe_static_results(*args, **kwargs):
    return _sync_result_dependencies().import_fe_static_results(*args, **kwargs)


def import_fe_static_results_from_project_result(*args, **kwargs):
    return _sync_result_dependencies().import_fe_static_results_from_project_result(*args, **kwargs)


def get_fe_static_results(*args, **kwargs):
    return _sync_result_dependencies().get_fe_static_results(*args, **kwargs)


def _build_static_alignment(*args, **kwargs):
    return _sync_correlation_dependencies()._build_static_alignment(*args, **kwargs)


def _build_static_analysis_error_rows(*args, **kwargs):
    return _sync_correlation_dependencies()._build_static_analysis_error_rows(*args, **kwargs)


def store_updated_static_analysis_error(*args, **kwargs):
    return _sync_correlation_dependencies().store_updated_static_analysis_error(*args, **kwargs)


def compute_static_correlation(*args, **kwargs):
    return _sync_correlation_dependencies().compute_static_correlation(*args, **kwargs)


def evaluate_static_correlation(*args, **kwargs):
    return _sync_correlation_dependencies().evaluate_static_correlation(*args, **kwargs)


def _load_modal_correlation_rows(*args, **kwargs):
    return _sync_correlation_dependencies()._load_modal_correlation_rows(*args, **kwargs)


def _ensure_modal_correlation_rows(*args, **kwargs):
    return _sync_correlation_dependencies()._ensure_modal_correlation_rows(*args, **kwargs)


def compute_modal_correlation(*args, **kwargs):
    return _sync_correlation_dependencies().compute_modal_correlation(*args, **kwargs)


def get_modal_correlation(*args, **kwargs):
    return _sync_correlation_dependencies().get_modal_correlation(*args, **kwargs)


def get_modal_correlation_matrix_payload(*args, **kwargs):
    return _sync_correlation_dependencies().get_modal_correlation_matrix_payload(*args, **kwargs)


def get_modal_correlation_table_payload(*args, **kwargs):
    return _sync_correlation_dependencies().get_modal_correlation_table_payload(*args, **kwargs)


def get_modal_match_frequency_scatter_payload(*args, **kwargs):
    return _sync_correlation_dependencies().get_modal_match_frequency_scatter_payload(*args, **kwargs)


def get_modal_frequency_consistency_payload(*args, **kwargs):
    return _sync_correlation_dependencies().get_modal_frequency_consistency_payload(*args, **kwargs)


def get_modal_correlation_all_scatter_payload(*args, **kwargs):
    return _sync_correlation_dependencies().get_modal_correlation_all_scatter_payload(*args, **kwargs)


def preview_modal_match(*args, **kwargs):
    return _sync_correlation_dependencies().preview_modal_match(*args, **kwargs)


def match_modal_modes(*args, **kwargs):
    return _sync_correlation_dependencies().match_modal_modes(*args, **kwargs)
