from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.realtime.manager import manager
from app.routers import ws



router = APIRouter()

@router.websocket("/ws/trips/{trip_id}")
async def trip_ws(ws: WebSocket, trip_id: str):
    await manager.connect(trip_id, ws)
    try:
        while True:
            # keep connection alive; client can send pings
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(trip_id, ws)
