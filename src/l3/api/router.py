from fastapi import APIRouter
from .routes import health, jobs, meta, query, results, geometry, modal, color_code, section, raw_results

router = APIRouter()
router.include_router(health.router)
router.include_router(jobs.router)
router.include_router(meta.router)
router.include_router(query.router)
router.include_router(results.router)
router.include_router(raw_results.router)
router.include_router(geometry.router)
router.include_router(modal.router)
router.include_router(color_code.router)
router.include_router(section.router)
