from fastapi import APIRouter

from src.agent import api as agent_api
from src.api.v1 import auth, me, tenants, users
from src.connectors import api as connectors_api
from src.contacts import api as contacts_api
from src.inbox import api as inbox_api
from src.knowledge import api as knowledge_api
from src.wa import api as wa_api

router = APIRouter(prefix="/v1")

router.include_router(auth.router)
router.include_router(tenants.router)
router.include_router(users.router)
router.include_router(me.router)
router.include_router(wa_api.router)
router.include_router(agent_api.router)
router.include_router(contacts_api.router)
router.include_router(connectors_api.router)
router.include_router(inbox_api.router)
router.include_router(knowledge_api.router)
