from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


templates = Jinja2Templates(directory=str(__import__("pathlib").Path(__file__).resolve().parents[1] / "templates"))
router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def console_home(request: Request):
    return templates.TemplateResponse(request, "index.html")
