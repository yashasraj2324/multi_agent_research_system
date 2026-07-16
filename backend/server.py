"""Placeholder ASGI app — the real app is Streamlit on port 3000."""
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def root(request):
    return JSONResponse({"status": "ok", "app": "streamlit-on-3000"})


app = Starlette(routes=[Route("/api/", root)])
