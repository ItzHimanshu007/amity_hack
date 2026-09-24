"""FastAPI app entry point for Nagar Naadi. Wires routers; holds no business logic."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Nagar Naadi", version="0.1.0")

# The frontend is served from a separate `python -m http.server` port, so it is
# cross-origin. Hackathon-wide open policy; tighten if this ever leaves a laptop.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Liveness probe. Everyone uses this to confirm the backend is up."""
    return {"status": "ok"}


# Phase 6 mounts the real routers here, e.g.:
#   from api.routes import router as routes_router
#   app.include_router(routes_router)
