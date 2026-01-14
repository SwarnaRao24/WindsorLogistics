from fastapi import APIRouter, Depends, HTTPException
from app.auth.deps import require_roles
from app.db.mongo import db
from app.realtime.manager import manager
from app.schemas.trip import LocationUpdate

import time

router = APIRouter()


def compute_delay(planned_eta_ms: int | None, now_ms: int) -> tuple[int | None, str | None]:
    if not planned_eta_ms:
        return None, None

    delay_min = int((now_ms - planned_eta_ms) / 60000)

    # early or on-time
    if delay_min <= 5:
        return delay_min, "green"
    if delay_min <= 20:
        return delay_min, "yellow"
    return delay_min, "red"


@router.post("/trips/{trip_id}/location")
async def update_location(trip_id: str, loc: LocationUpdate, user=Depends(require_roles("driver"))):
    # ensure trip exists
    trip = await db().trips.find_one({"trip_id": trip_id})
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    now_ms = int(time.time() * 1000)
    ts = loc.ts or now_ms

    # save location history
    await db().locations.insert_one({
        "trip_id": trip_id,
        "lat": loc.lat,
        "lng": loc.lng,
        "ts": ts,
        "speed": loc.speed,
        "driver": user["sub"],
    })

    delay_minutes, delay_color = compute_delay(trip.get("planned_eta_ms"), now_ms)

    # update trip summary fields (for public read)
    update_doc = {
        "last_location": {"lat": loc.lat, "lng": loc.lng, "speed": loc.speed, "ts": ts},
        "last_update_ms": now_ms,
        "delay_minutes": delay_minutes,
        "delay_color": delay_color,
    }

    # auto status: scheduled -> in_transit
    if trip.get("status") == "scheduled":
        update_doc["status"] = "in_transit"

    await db().trips.update_one({"trip_id": trip_id}, {"$set": update_doc})

    # broadcast to websocket subscribers
    payload = {
        "trip_id": trip_id,
        "lat": loc.lat,
        "lng": loc.lng,
        "speed": loc.speed,
        "ts": ts,
        "delay_minutes": delay_minutes,
        "delay_color": delay_color,
    }
    await manager.broadcast(trip_id, payload)

    return {"ok": True}
