import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.auth.dependencies import get_current_user
from src.db.models import User

router = APIRouter(prefix="/me", tags=["me"])


class MeResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=MeResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
