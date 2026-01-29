from fastapi import APIRouter

from .v1.admin_auth import router as admin_auth_router
from .v1.admin_export import router as admin_export_router
from .v1.admin_instances import router as admin_instances_router
from .v1.attendance import router as attendance_router
from .v1.instances import router as instances_router


def build_api_router() -> APIRouter:
    """Build and return the root API router.

    All API endpoints are mounted under /api in Nginx, so the FastAPI app mounts
    this router under prefix="/api" and then version routers under "/v1".
    """

    api = APIRouter()

    v1 = APIRouter(prefix="/v1")
    v1.include_router(instances_router, tags=["instances"])
    v1.include_router(attendance_router, tags=["attendance"])
    v1.include_router(admin_auth_router, tags=["admin-auth"])
    v1.include_router(admin_instances_router, tags=["admin-instances"])
    v1.include_router(admin_export_router, tags=["admin-export"])

    api.include_router(v1)
    return api
