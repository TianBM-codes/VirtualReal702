from fastapi import APIRouter

from .routers import (
    fem_router,
    matching_router,
    optimization_router,
    sensitivity_router,
    system_router,
    test_data_router,
)

router = APIRouter()
router.include_router(system_router)
router.include_router(test_data_router)
router.include_router(fem_router)
router.include_router(optimization_router)
router.include_router(matching_router)
router.include_router(sensitivity_router)
