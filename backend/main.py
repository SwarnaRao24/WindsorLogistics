from pathlib import Path
from dotenv import load_dotenv

# backend/main.py
BASE_DIR = Path(__file__).resolve().parent          # .../WindsorLogistics/backend
REPO_DIR = BASE_DIR.parent                          # .../WindsorLogistics
FRONTEND_DIR = REPO_DIR / "frontend"                # .../WindsorLogistics/frontend

# Load env from backend/.env (matches your folder structure)
load_dotenv(BASE_DIR / ".env")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routers import auth, trips, trucks, tracking, ws


def create_app() -> FastAPI:
    app = FastAPI()

    # CORS (MVP ok; tighten later)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static mount (serves /static/track.html, /static/owner_trips.html, etc.)
    if not FRONTEND_DIR.exists():
        # Fail fast with clear error
        raise RuntimeError(f"Frontend directory not found: {FRONTEND_DIR}")

    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    # Routers
    app.include_router(auth.router, prefix="/api")
    app.include_router(trips.router, prefix="/api")
    app.include_router(trucks.router, prefix="/api")
    app.include_router(tracking.router, prefix="/api")
    app.include_router(ws.router)  # websocket routes already have full paths

    # Pages / health
    @app.get("/")
    def root():
        # Better demo: open the owner page by default
        return RedirectResponse(url="/static/owner_trips.html")

    @app.get("/health")
    def health():
        return {"ok": True}

    # Optional direct route (not required since /static already serves it)
    @app.get("/owner_trips.html")
    def owner_trips_page():
        return FileResponse(FRONTEND_DIR / "owner_trips.html")

    return app


app = create_app()
