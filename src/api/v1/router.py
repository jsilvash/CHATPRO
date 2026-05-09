from fastapi import APIRouter

from src.api.v1 import auth, me, tenants, users

router = APIRouter(prefix="/v1")

router.include_router(auth.router)
router.include_router(tenants.router)
router.include_router(users.router)
router.include_router(me.router)
