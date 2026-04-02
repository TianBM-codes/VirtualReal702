from fastapi import APIRouter, Request

from services.model_update.analysis.solver_service import (
    run_abaqus_adjoint_job,
    run_abaqus_sensitivity_job,
    run_nastran_sol103_job,
)
from src.l3.core.errors import AppError

from ..common import server_error
from ..models import (
    AbaqusAdjointRunRequest,
    AbaqusSensitivityRunRequest,
    NastranSol103RunRequest,
)
from ..utils import log_request, model_to_dict

router = APIRouter(tags=["solver"])


@router.post("/solver/abaqus/sensitivity")
async def run_abaqus_sensitivity_api(request: Request, body: AbaqusSensitivityRunRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = run_abaqus_sensitivity_job(
            input_inp=body.input_inp,
            output_dir=body.output_dir,
            response_elset=body.response_elset,
            response_nset=body.response_nset,
            response_frequency=body.response_frequency,
            node_vars=body.node_vars,
            element_vars=body.element_vars,
            abaqus=body.abaqus,
            job_name=body.job_name,
            cpus=body.cpus,
            interactive=body.interactive,
            run_solver=body.run_solver,
            timeout_sec=body.timeout_sec,
            extra_args=body.extra_args,
        )
        return {"ok": True, "message": "abaqus sensitivity workflow success", "data": data}
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/solver/abaqus/adjoint")
async def run_abaqus_adjoint_api(request: Request, body: AbaqusAdjointRunRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = run_abaqus_adjoint_job(
            input_inp=body.input_inp,
            output_inp=body.output_inp,
            response_nset=body.response_nset,
            abaqus=body.abaqus,
            job_name=body.job_name,
            cpus=body.cpus,
            interactive=body.interactive,
            run_solver=body.run_solver,
            timeout_sec=body.timeout_sec,
            extra_args=body.extra_args,
        )
        return {"ok": True, "message": "abaqus adjoint workflow success", "data": data}
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc


@router.post("/solver/nastran/sol103")
async def run_nastran_sol103_api(request: Request, body: NastranSol103RunRequest):
    await log_request(request, model_to_dict(body))
    try:
        data = run_nastran_sol103_job(
            input_bdf=body.input_bdf,
            output_bdf=body.output_bdf,
            settings=body.settings,
            nastran=body.nastran,
            run_solver=body.run_solver,
            timeout_sec=body.timeout_sec,
            extra_args=body.extra_args,
        )
        return {"ok": True, "message": "nastran sol103 workflow success", "data": data}
    except AppError:
        raise
    except Exception as exc:
        raise server_error(exc) from exc
