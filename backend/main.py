# main.py
from dotenv import load_dotenv
load_dotenv()

import os
import uuid
import certifi
from datetime import date, datetime, timezone
from typing import Literal, Optional, List, Dict, Set
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ReturnDocument


# -----------------------------
# App + CORS
# -----------------------------
app = FastAPI()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

if not FRONTEND_DIR.exists():
    raise RuntimeError(f"Frontend folder not found at: {FRONTEND_DIR}")

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

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
TruckStatus = Literal["available", "out_of_service", "booked"]  # <-- THIS LINE GOES HERE

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"

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
async def ensure_indexes():
    # trucks
    await db.trucks.create_index(
        [("owner_id", 1), ("truck_id", 1)],
        unique=True,
        name="uniq_owner_truck"
    )
    await db.trucks.create_index([("truck_id", 1)], name="idx_truck_id")

    # bookings (strict)
    await db.bookings.create_index([("booking_id", 1)], unique=True, name="uniq_booking_id")
    await db.bookings.create_index([("trip_id", 1)], unique=True, name="uniq_booking_trip_id")
    await db.bookings.create_index([("truck_id", 1), ("created_at", -1)], name="idx_booking_truck_created")

    # trips (strict)
    await db.trips.create_index([("trip_id", 1)], unique=True, name="uniq_trip_id")
    await db.trips.create_index([("owner_id", 1), ("created_at", -1)], name="idx_trip_owner_created")
    await db.trips.create_index([("truck_id", 1), ("created_at", -1)], name="idx_trip_truck_created")

@app.on_event("startup")
async def startup():
    await client.admin.command("ping")   # hard fail if DB down
    await ensure_indexes()
    print("✅ Mongo connected + indexes ready")


# -----------------------------
# Basics
# -----------------------------
@app.get("/")
async def root():
    return {"message": "API is running. Go to /docs"}

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/owner.html")
def owner_html_page():
    return FileResponse(FRONTEND_DIR / "owner.html")

@app.get("/customer.html")
def customer_html_page():
    return FileResponse(FRONTEND_DIR / "customer.html")
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
@app.post("/api/bookings")
async def create_booking(payload: BookingCreate):

    now = datetime.now(timezone.utc)

    # ✅ Atomic lock: only one request can book the truck
    truck = await db.trucks.find_one_and_update(
        {"truck_id": payload.truck_id, "status": "available"},
        {"$set": {"status": "booked", "updated_at": now}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not truck:
        raise HTTPException(status_code=400, detail="Truck not available")

    booking_id = new_id("bk")
    trip_id = new_id("tr")

    booking_doc = {
        "booking_id": booking_id,
        "trip_id": trip_id,
        "truck_id": payload.truck_id,
        "customer_name": payload.customer_name,
        "pickup_location": payload.pickup_location,
        "drop_location": payload.drop_location,
        "booking_date": payload.booking_date,
        "booking_time": payload.booking_time,
        "status": "created",
        "created_at": now
    }

    result1 = await db.bookings.insert_one(booking_doc)

    trip_doc = {
        "trip_id": trip_id,
        "booking_id": booking_id,
        "owner_id": truck["owner_id"],
        "truck_id": payload.truck_id,
        "status": "scheduled",
        "created_at": now,
        "updated_at": now
    }
    result2 = await db.trips.insert_one(trip_doc)

    return {
        "ok": True,
        "booking": {**booking_doc, "_id": str(result1.inserted_id)},
        "trip": {**trip_doc, "_id": str(result2.inserted_id)},
    }


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
        "ts": datetime.now(timezone.utc),
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

@app.get("/api/owners/me/trips")
async def owner_list_trips():
    cursor = db.trips.find({"owner_id": OwnerID}, {"_id": 0}).sort("created_at", -1)
    return await cursor.to_list(length=500)

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
