# WindsorLogistics
An application built to help local logistic and truck owners and drivers in Windsor, ON to share the live tracking updates of the truck by drivers to owners and clients/customers.

## Features (MVP)
- Driver live GPS sharing
- Owner real-time truck tracking
- WebSocket-based live updates
- FastAPI backend
- MongoDB (NoSQL)

## Tech Stack
- Backend: FastAPI (Python)
- Database: MongoDB
- Frontend: HTML, JS (responsive)
- Realtime: WebSockets

## How to Run Locally

### Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
