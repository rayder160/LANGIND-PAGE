from fastapi import APIRouter
from app.routes.admin import areas, users, stats, analytics

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(areas.router)
router.include_router(users.router)
router.include_router(stats.router)
router.include_router(analytics.router)
