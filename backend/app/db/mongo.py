import os, certifi
from motor.motor_asyncio import AsyncIOMotorClient

_client = None

def db():
    global _client
    if _client is None:
        url = os.getenv("MONGO_URL")
        if not url:
            raise RuntimeError("MONGO_URL not set")
        _client = AsyncIOMotorClient(url, tlsCAFile=certifi.where())
    return _client["windsorlogistics"]
