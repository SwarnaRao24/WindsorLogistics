from typing import Dict, Set
from fastapi import WebSocket

class WSManager:
    def __init__(self):
        self.rooms: Dict[str, Set[WebSocket]] = {}

    async def connect(self, trip_id: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(trip_id, set()).add(ws)

    def disconnect(self, trip_id: str, ws: WebSocket):
        if trip_id in self.rooms:
            self.rooms[trip_id].discard(ws)
            if not self.rooms[trip_id]:
                self.rooms.pop(trip_id, None)

    async def broadcast(self, trip_id: str, message: dict):
        for ws in list(self.rooms.get(trip_id, set())):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(trip_id, ws)

manager = WSManager()
