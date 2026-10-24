from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class CheckInRequest(BaseModel):
    senior_id: str = Field(min_length=1)
    retry_rejected: bool = Field(default=False, strict=True)


@router.put("/profiles/{senior_id}")
def profile(senior_id: str, payload: dict, request: Request):
    if "senior_id" in payload and payload["senior_id"] != senior_id:
        raise ValueError("Profile senior_id does not match URL")
    return request.app.state.service.put_profile({**payload, "senior_id": senior_id})


@router.post("/calls")
def start_call(payload: CheckInRequest, request: Request):
    return request.app.state.service.start_check_in(payload.senior_id, payload.retry_rejected)


@router.get("/history")
def history(request: Request):
    return request.app.state.service.snapshot()


@router.post("/scheduler/tick")
def tick(request: Request):
    request.app.state.service.tick()
    return {"ok": True}
