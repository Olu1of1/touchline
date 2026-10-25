"""Web server for the Touchline player.

    python -m touchline.server            # http://localhost:8000
    PORT=8080 python -m touchline.server
"""

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm, tts
from .data import league, match_list
from .pipeline import get_timeline

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
RECOMMENDED = ["M059", "M346", "M097", "M222"]     # chosen for drama; scores are never shown before listening

app = FastAPI(title="Touchline")
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/api/config")
def config():
    backend = llm.get_backend()
    return {"tts": "azure" if tts.available() else "browser", "voice": tts.voice() if tts.available() else None,
            "colour": backend.name if backend else "templates (offline)", "ask": asker().backend,
            "research": desk().backend}


@app.get("/api/matches")
def matches():
    ms = match_list()
    for m in ms:
        m["recommended"] = m["match_id"] in RECOMMENDED
    return ms


@app.get("/api/timeline/{match_id}")
def timeline(match_id: str):
    match_id = match_id.upper()
    if match_id not in set(league()["matches"].match_id):
        raise HTTPException(404, f"No match {match_id}. Match IDs run M001 to M380.")
    return get_timeline(match_id)


_ask = None


def asker():
    global _ask
    if _ask is None:
        from .agents import AskTouchline
        _ask = AskTouchline()
    return _ask


class AskRequest(BaseModel):
    match_id: str
    t: float
    question: str


@app.post("/api/ask")
def ask(req: AskRequest):
    """Answer a listener's question about the match as it stands at broadcast time t (nothing later is visible)."""
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Ask a question about the match.")
    if len(q) > 300:
        raise HTTPException(400, "Please keep questions under 300 characters.")
    mid = req.match_id.upper()
    if mid not in set(league()["matches"].match_id):
        raise HTTPException(404, f"No match {mid}.")
    return asker().ask(mid, max(0.0, req.t), q)


_desk = None


def desk():
    global _desk
    if _desk is None:
        from .research import ResearchDesk
        _desk = ResearchDesk()
    return _desk


@app.get("/research")
def research_page():
    return FileResponse(os.path.join(WEB, "research.html"))


class Turn(BaseModel):
    q: str
    a: str


class ResearchRequest(BaseModel):
    question: str
    history: list[Turn] = []


@app.post("/api/research")
def research(req: ResearchRequest):
    """Season-wide questions for the research desk. Answers come with result cards (table + chart) and tool calls."""
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Ask a question about the season.")
    if len(q) > 400:
        raise HTTPException(400, "Please keep questions under 400 characters.")
    return desk().ask(q, [t.model_dump() for t in req.history[-3:]])


class TTSRequest(BaseModel):
    text: str
    excite: float = 0.0
    rate: float = 1.0


@app.post("/api/tts")
def speak(req: TTSRequest):
    if not tts.available():
        raise HTTPException(404, "Azure Speech is not configured; the player uses the browser voice.")
    if len(req.text) > 600:
        raise HTTPException(400, "Line too long.")
    try:
        audio = tts.synthesize(req.text, req.excite, req.rate)
    except Exception as exc:
        raise HTTPException(502, f"Azure Speech error: {exc}")
    return Response(audio, media_type="audio/mpeg")


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    league()   # load data once at start-up
    print(f"Touchline running at http://localhost:{port}")
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=port)


if __name__ == "__main__":
    main()
