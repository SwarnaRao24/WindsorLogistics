from pydantic import BaseModel
from typing import Optional, Literal

TripStatus = Literal["scheduled", "in_transit", "delayed", "delivered", "cancelled"]


class TripCreate(BaseModel):
    trip_id: str
    customer_id: str
    driver_id: str
    truck_id: Optional[str] = None
    planned_eta_ms: Optional[int] = None
    status: TripStatus = "scheduled"


class TripPatch(BaseModel):
    status: Optional[TripStatus] = None
    planned_eta_ms: Optional[int] = None


class LocationUpdate(BaseModel):
    lat: float
    lng: float
    ts: Optional[int] = None
    speed: Optional[float] = None
