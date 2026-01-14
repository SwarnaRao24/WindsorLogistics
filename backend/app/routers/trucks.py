from fastapi import APIRouter, Depends
from app.auth.deps import require_roles

router = APIRouter()

@router.get("/trucks")
def list_trucks(user=Depends(require_roles("owner"))):
    return []
