"""The browser front end: one page, a small JSON API, and a WebSocket of feed events."""

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.datastructures import Headers
from starlette.websockets import WebSocketClose

from examples.rumor_mill.feed import Event, Feed
from examples.rumor_mill.game import Game
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.player import Player, Refusal
from examples.rumor_mill.suspicion import HEAT_LINES, Suspicion
from examples.rumor_mill.town import PLACES, Town
from examples.rumor_mill.usage import Usage

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "index.html"
MAX_BODY_BYTES = 8192
VILLAGER_POLL_SECONDS = 1.0
USAGE_POLL_SECONDS = 3.0
BAD_WHISPER = 'Send JSON like {"name": "Marta", "text": "the miller waters his flour"}.'


class Guard:
    """Refuse requests for other hosts (DNS rebinding) and writes from other origins."""

    def __init__(self, app, port: int):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            origin = headers.get("origin")
            writes = scope["type"] == "websocket" or scope["method"] not in ("GET", "HEAD")
            if host not in self.hosts or (writes and origin is not None and origin != f"http://{host}"):
                if scope["type"] == "websocket":
                    await WebSocketClose(1008)(scope, receive, send)
                else:
                    await PlainTextResponse("Forbidden", 403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def villagers(town: Town) -> list[dict]:
    return sorted(
        ({"id": v.id, "name": v.name, "occupation": v.occupation, "place": town.place_of(v.id)}
         for v in town.villagers),
        key=lambda v: v["name"],
    )


def event_json(event: Event) -> dict:
    """The event as the page gets it; a heat event also carries its sentence."""
    data = event.to_json()
    if event.kind == "heat":
        data["text"] = HEAT_LINES[event.data["label"]]
    return data


def game_json(game: Game) -> dict:
    """The game state and, once it is over, only the outcome, goals and reasons."""
    s = game.summary
    summary = s and {"outcome": s.outcome, "goals": [[text, percent] for text, percent in s.goals], "reasons": s.reasons}
    return {"state": game.state, "summary": summary}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status)


def create_app(
    *, player: Player, town: Town, feed: Feed, judge, goals: Sequence[Goal], usage: Usage, port: int,
    suspicion: Suspicion, game: Game,
) -> FastAPI:
    # ponytail: unbounded per-client queues; a stalled tab on localhost is the only consumer.
    clients: set[asyncio.Queue] = set()
    boot = uuid.uuid4().hex

    def broadcast(message: dict) -> None:
        for queue in clients:
            queue.put_nowait(message)

    def on_event(event: Event) -> None:
        broadcast(event_json(event))

    async def watch(kind: str, snapshot, seconds: float) -> None:
        """Send the snapshot whenever it has changed, checking every few seconds."""
        last = snapshot()
        while True:
            await asyncio.sleep(seconds)
            now = snapshot()
            if now != last:
                last = now
                broadcast({"kind": kind, kind: now})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        feed.sinks.append(on_event)
        watchers = [
            asyncio.create_task(watch("villagers", lambda: villagers(town), VILLAGER_POLL_SECONDS)),
            asyncio.create_task(watch("usage", usage.to_json, USAGE_POLL_SECONDS)),
        ]
        try:
            yield
        finally:
            feed.sinks.remove(on_event)
            for w in watchers:
                w.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, port=port)
    app.state.clients = clients

    @app.get("/")
    async def page():
        return FileResponse(PAGE)

    @app.get("/api/state")
    async def state():
        return {
            "boot": boot,
            "places": list(PLACES),
            "goals": [
                {"id": g.id, "text": g.text, "percent": int(judge.progress.get(g.id, 0.0) * 100),
                 "met": g.id in judge.met}
                for g in goals
            ],
            "villagers": villagers(town),
            "events": [event_json(e) for e in feed.recent],
            "usage": usage.to_json(),
            "heat": (label := suspicion.label()),
            "heat_line": HEAT_LINES[label],
            "game": game_json(game),
        }

    @app.get("/api/villagers/{vid}")
    async def inspect(vid: str):
        if vid not in town:
            name = town.name_of(vid)
            return _error(404, f"{name} has left town." if name else "No villager with that id.")
        v = town.get(vid)
        return {
            "id": v.id,
            "name": v.name,
            "occupation": v.occupation,
            "place": town.place_of(v.id),
            "personality": v.personality,
            "grudge": v.grudge,
            "relationships": [
                {"id": rid, "name": town.name_of(rid) or "someone who left", "kind": kind}
                for rid, kind in v.relationships.items()
            ],
        }

    @app.post("/api/whisper")
    async def whisper(request: Request):
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                return _error(413, "That whisper is too long.")
        try:
            payload = json.loads(body)
            text, vid, name = payload["text"], payload.get("villager_id"), payload.get("name", "")
        except (ValueError, KeyError, TypeError, AttributeError):
            return _error(400, BAD_WHISPER)
        if not all(isinstance(x, str) for x in (text, name)) or not isinstance(vid, str | None):
            return _error(400, BAD_WHISPER)
        if problem := await (player.whisper_to(vid, text) if vid else player.whisper(name, text)):
            return _error(409 if isinstance(problem, Refusal) else 400, problem)
        return {"ok": True}

    @app.websocket("/ws")
    async def events(socket: WebSocket):
        """Send events until the client goes away; reading is what notices a closed tab."""
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        clients.add(queue)

        async def pump() -> None:
            while True:
                await socket.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while (await socket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            clients.discard(queue)
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    return app
