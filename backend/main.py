from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers import auth, trips, trucks, tracking, ws



def create_app() -> FastAPI:
    app = FastAPI()

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Frontend folder (repo root/frontend)
    FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

    # Static mount (optional but fine)
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    # Routers
    app.include_router(auth.router, prefix="/api")
    app.include_router(trips.router, prefix="/api")
    app.include_router(trucks.router, prefix="/api")
    app.include_router(tracking.router, prefix="/api")
    app.include_router(ws.router)  # websocket routes usually already have full paths

    # Pages / health
    @app.get("/")
    def root():
        return {"ok": True, "msg": "API running. Go to /docs"}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/owner_trips.html")
    def owner_trips_page():
        return FileResponse(FRONTEND_DIR / "owner_trips.html")

    return app


app = create_app()
