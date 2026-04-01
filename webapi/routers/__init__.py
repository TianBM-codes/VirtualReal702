from .fem import router as fem_router
from .matching import router as matching_router
from .optimization import router as optimization_router
from .sensitivity import router as sensitivity_router
from .system import router as system_router
from .test_data import router as test_data_router

__all__ = [
    "fem_router",
    "matching_router",
    "optimization_router",
    "sensitivity_router",
    "system_router",
    "test_data_router",
]
