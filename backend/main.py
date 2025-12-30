# main.py
from dotenv import load_dotenv
load_dotenv()

import os
import certifi
from datetime import date, datetime, timezone
from typing import Literal, Optional, List, Dict, Set
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ReturnDocument


# -----------------------------
# App + CORS
# -----------------------------
app = FastAPI()

# Allow http://localhost:anyport and http://127.0.0.1:anyport
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# MongoDB
# -----------------------------
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("MONGO_URL not set. Create backend/.env with MONGO_URL=...")

client = AsyncIOMotorClient(MONGO_URL, tlsCAFile=certifi.where())
db = client["truck_tracker"]

# -----------------------------
# Realtime subscribers (in-memory)
# -----------------------------
subscribers: Dict[str, Set[WebSocket]] = {}

# -----------------------------
# Models / Types
# -----------------------------
OwnerID = "owner-1"

TruckType = Literal["pickup", "box", "semi"]
TruckStatus = Literal["available", "out_of_service", "unavailable"]  # <-- THIS LINE GOES HERE

class TruckCreate(BaseModel):
    truck_id: str = Field(min_length=2, max_length=64)
    type: TruckType
    status: TruckStatus = "available"

class TruckUpdate(BaseModel):
    status: Optional[TruckStatus] = None

class TruckOut(BaseModel):
    truck_id: str
    owner_id: str
    type: TruckType
    status: TruckStatus
    created_at: datetime
    updated_at: datetime

class TruckPublic(BaseModel):
    truck_id: str
    type: TruckType
    status: Literal["available"]

class BookingCreate(BaseModel):
    truck_id: str
    customer_name: str
    pickup_location: str
    drop_location: str
    booking_date: Optional[str] = None  # YYYY-MM-DD
    booking_time: str

class BookingOut(BaseModel):
    booking_id: str
    truck_id: str
    customer_name: str
    pickup_location: str
    drop_location: str
    booking_date: date
    booking_time: str
    status: str
    created_at: datetime


# -----------------------------
# Startup: indexes (safe)
# -----------------------------
@app.on_event("startup")
async def startup():
    # trucks: unique per owner
    await db.trucks.create_index([("owner_id", 1), ("truck_id", 1)], unique=True)
    await db.trucks.create_index([("truck_id", 1)])
    await db.truck_current_location.create_index([("truck_id", 1)], unique=True)

    # bookings: safe unique booking_id (partial index avoids old docs missing booking_id)
    # NOTE: if you already had a broken "booking_id_1" unique index, DROP IT in Atlas first.
    await db.bookings.create_index(
        [("booking_id", 1)],
        unique=True,
        partialFilterExpression={"booking_id": {"$exists": True, "$type": "string"}},
    )
    await db.bookings.create_index([("truck_id", 1)])


# -----------------------------
# Basics
# -----------------------------
@app.get("/")
async def root():
    return {"message": "API is running. Go to /docs"}

@app.get("/health")
async def health():
    return {"ok": True}


# -----------------------------
# Owner Fleet
# -----------------------------
@app.post("/api/owners/me/trucks", response_model=TruckOut)
async def create_truck(body: TruckCreate):
    truck_id = body.truck_id.strip()
    now = datetime.now(timezone.utc)


    doc = {
        "owner_id": OwnerID,
        "truck_id": truck_id,
        "type": body.type,
        "status": body.status,
        "created_at": now,
        "updated_at": now,
    }

    try:
        await db.trucks.insert_one(doc)
    except Exception:
        raise HTTPException(status_code=409, detail="truck_id already exists for this owner")

    doc.pop("_id", None)
    return doc

@app.get("/api/owners/me/trucks", response_model=List[TruckOut])
async def list_trucks():
    cursor = db.trucks.find({"owner_id": OwnerID}, {"_id": 0}).sort("created_at", 1)
    return await cursor.to_list(length=500)

@app.patch("/api/trucks/{truck_id}", response_model=TruckOut)
async def update_truck(truck_id: str, body: TruckUpdate):
    truck_id = truck_id.strip()

    update: dict = {}
    if body.status is not None:
        update["status"] = body.status

    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")

    update["updated_at"] = datetime.now(timezone.utc)


    updated = await db.trucks.find_one_and_update(
        {"owner_id": OwnerID, "truck_id": truck_id},
        {"$set": update},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Truck not found")

    return updated


# -----------------------------
# Customer: available trucks
# -----------------------------
@app.get("/api/trucks/available", response_model=List[TruckPublic])
async def get_available_trucks():
    cursor = db.trucks.find(
        {"status": "available"},
        {"_id": 0, "truck_id": 1, "type": 1, "status": 1}
    )
    return await cursor.to_list(length=500)


# -----------------------------
# Customer: booking (FIXED: atomic lock)
# -----------------------------
@app.post("/api/bookings", response_model=BookingOut)
async def create_booking(payload: BookingCreate):
    truck_id = payload.truck_id.strip()
    now = datetime.now(timezone.utc)

    # 1) ATOMIC LOCK: only one request can flip available -> unavailable
    locked_truck = await db.trucks.find_one_and_update(
        {"truck_id": payload.truck_id, "status": "available"},
        {"$set": {"status": "unavailable", "updated_at": datetime.now(timezone.utc)}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )

    if not locked_truck:
        # If it wasn't available, do NOT create booking.
        raise HTTPException(status_code=400, detail="Truck not available")

    booking_id = f"bk-{uuid4().hex[:10]}"

   
    # 2) Create booking AFTER locking
    booking = {
    "booking_id": booking_id,
        "truck_id": payload.truck_id,
        "customer_name": payload.customer_name,
        "pickup_location": payload.pickup_location,
        "drop_location": payload.drop_location,
        "booking_date": payload.booking_date,
        "booking_time": payload.booking_time,
        "status": "confirmed",
        "created_at": datetime.now(timezone.utc)
}


    await db.bookings.insert_one(booking)
    return booking


# -----------------------------
# Live Location (unchanged)
# -----------------------------
@app.post("/api/trucks/{truck_id}/location")
async def update_location(truck_id: str, payload: dict):
    lat = payload.get("lat")
    lng = payload.get("lng")
    speed = payload.get("speed")

    if lat is None or lng is None:
        raise HTTPException(status_code=400, detail="lat/lng required")

    doc = {
        "truck_id": truck_id,
        "lat": float(lat),
        "lng": float(lng),
        "speed": float(speed) if speed is not None else None,
        "ts": datetime.utcnow(),
    }

    await db.truck_current_location.update_one(
        {"truck_id": truck_id},
        {"$set": doc},
        upsert=True
    )

    if truck_id in subscribers:
        dead = []
        for ws in list(subscribers[truck_id]):
            try:
                await ws.send_json(doc)
            except Exception:
                dead.append(ws)
        for ws in dead:
            subscribers[truck_id].discard(ws)

    return {"ok": True}

@app.get("/api/trucks/{truck_id}/location")
async def get_location(truck_id: str):
    doc = await db.truck_current_location.find_one({"truck_id": truck_id}, {"_id": 0})
    return doc or {"truck_id": truck_id, "lat": None, "lng": None, "ts": None}

@app.websocket("/ws/trucks/{truck_id}")
async def ws_truck(websocket: WebSocket, truck_id: str):
    await websocket.accept()
    subscribers.setdefault(truck_id, set()).add(websocket)

    last = await db.truck_current_location.find_one({"truck_id": truck_id}, {"_id": 0})
    if last:
        await websocket.send_json(last)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers[truck_id].discard(websocket)
