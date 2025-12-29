# main.py
from dotenv import load_dotenv

load_dotenv()

import certifi
import os
from datetime import datetime
from typing import Literal, Optional, List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

# -----------------------------
# App + CORS
# -----------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# MongoDB (Atlas)
# -----------------------------
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("MONGO_URL not set. Create backend/.env with MONGO_URL=...")

client = AsyncIOMotorClient(MONGO_URL, tlsCAFile=certifi.where())
db = client["truck_tracker"]

# -----------------------------
# Realtime subscribers (in-memory)
# -----------------------------
subscribers: dict[str, set[WebSocket]] = {}

# -----------------------------
# Owner Fleet Models (MVP: hardcoded owner)
# -----------------------------
OwnerID = "owner-1"

TruckType = Literal["pickup", "box", "semi"]
TruckStatus = Literal["available", "out_of_service"]

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

# -----------------------------
# Startup: indexes
# -----------------------------
@app.on_event("startup")
async def startup():
    # Unique per owner so different owners can reuse truck IDs later
    await db.trucks.create_index([("owner_id", 1), ("truck_id", 1)], unique=True)
    # Optional: faster reads by truck_id
    await db.trucks.create_index([("truck_id", 1)])

# -----------------------------
# Basic endpoints
# -----------------------------
@app.get("/")
async def root():
    return {"message": "API is running. Go to /docs"}

@app.get("/health")
async def health():
    return {"ok": True}

# -----------------------------
# Owner Fleet Endpoints (no auth yet)
# -----------------------------
@app.post("/api/owners/me/trucks", response_model=TruckOut)
async def create_truck(body: TruckCreate):
    truck_id = body.truck_id.strip()

    now = datetime.utcnow()
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
    except Exception as e:
        # Duplicate key error message varies; keep it simple for now
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

    update["updated_at"] = datetime.utcnow()

    updated = await db.trucks.find_one_and_update(
        {"owner_id": OwnerID, "truck_id": truck_id},
        {"$set": update},
        projection={"_id": 0},
        return_document=True,
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Truck not found")

    return updated

# -----------------------------
# Live Location Endpoints
# -----------------------------
@app.post("/api/trucks/{truck_id}/location")
async def update_location(truck_id: str, payload: dict):
    """
    payload example:
    { "lat": 42.3149, "lng": -83.0364, "speed": 12.3 }
    """
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

    # save latest location (overwrite)
    await db.truck_current_location.update_one(
        {"truck_id": truck_id},
        {"$set": doc},
        upsert=True
    )

    # push to watchers via websocket
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

    # send last known immediately
    last = await db.truck_current_location.find_one({"truck_id": truck_id}, {"_id": 0})
    if last:
        await websocket.send_json(last)

    try:
        while True:
            # keep connection alive; we don't expect messages from client for now
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers[truck_id].discard(websocket)
