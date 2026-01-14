from fastapi import APIRouter, Depends, HTTPException
from app.auth.deps import require_roles
from app.db.mongo import db
from app.schemas.trip import TripCreate, TripPatch

import time
import secrets

router = APIRouter()


@router.post("/trips")
async def create_trip(payload: TripCreate, user=Depends(require_roles("owner"))):
    doc = payload.model_dump()
    doc["updated_at_ms"] = int(time.time() * 1000)

    await db().trips.update_one(
        {"trip_id": payload.trip_id},
        {"$set": doc},
        upsert=True,
    )
    return {"ok": True, "trip_id": payload.trip_id}


@router.get("/trips")
async def list_trips(user=Depends(require_roles("owner"))):
    trips = await db().trips.find({}, {"_id": 0}).to_list(length=200)
    return trips


@router.patch("/trips/{trip_id}")
async def patch_trip(trip_id: str, patch: TripPatch, user=Depends(require_roles("owner"))):
    update = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not update:
        return {"ok": True}

    update["updated_at_ms"] = int(time.time() * 1000)

    res = await db().trips.update_one({"trip_id": trip_id}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Trip not found")

    return {"ok": True}


# -------------------------
# OTP share link (MVP)
# -------------------------
@router.post("/trips/{trip_id}/share-otp")
async def create_share_otp(trip_id: str, user=Depends(require_roles("owner"))):
    trip = await db().trips.find_one({"trip_id": trip_id}, {"_id": 0})
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    otp = f"{secrets.randbelow(1000000):06d}"
    now_ms = int(time.time() * 1000)
    expires_ms = now_ms + (15 * 60 * 1000)

    await db().trip_shares.update_one(
        {"trip_id": trip_id},
        {"$set": {"trip_id": trip_id, "otp": otp, "expires_ms": expires_ms, "created_at_ms": now_ms}},
        upsert=True,
    )

    return {"ok": True, "otp": otp, "expires_ms": expires_ms}


@router.get("/public/resolve-otp")
async def resolve_otp(otp: str):
    now_ms = int(time.time() * 1000)
    share = await db().trip_shares.find_one({"otp": otp}, {"_id": 0})
    if not share:
        raise HTTPException(status_code=404, detail="Invalid OTP")

    if share["expires_ms"] < now_ms:
        raise HTTPException(status_code=410, detail="OTP expired")

    return {"trip_id": share["trip_id"], "expires_ms": share["expires_ms"]}


@router.get("/public/trips/{trip_id}")
async def public_trip(trip_id: str):
    trip = await db().trips.find_one({"trip_id": trip_id}, {"_id": 0})
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    return {
        "trip_id": trip.get("trip_id"),
        "status": trip.get("status"),
        "planned_eta_ms": trip.get("planned_eta_ms"),
        "customer_id": trip.get("customer_id"),
        "driver_id": trip.get("driver_id"),
        "truck_id": trip.get("truck_id"),
        "last_location": trip.get("last_location"),
        "last_update_ms": trip.get("last_update_ms"),
        "delay_minutes": trip.get("delay_minutes"),
        "delay_color": trip.get("delay_color"),
    }
