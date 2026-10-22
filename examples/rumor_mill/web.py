"""The browser front end: one page, a small JSON API, and a WebSocket of feed events."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.datastructures import Headers
from starlette.websockets import WebSocketClose

from examples.rumor_mill.feed import Event, Feed
from examples.rumor_mill.game import Game
from examples.rumor_mill.goals import Goal, claim
from examples.rumor_mill.hints import Hints
from examples.rumor_mill.notes import Notes
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Player, Refusal
from examples.rumor_mill.players import RUN_OUT_LOCKOUT_SECONDS, Players, new_owner_id
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.suspicion import HEAT_LINES, Suspicion
from examples.rumor_mill.town import PLACES, Town
from examples.rumor_mill.usage import Usage, charging

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "index.html"
MAX_BODY_BYTES = 8192
VILLAGER_POLL_SECONDS = 1.0
USAGE_POLL_SECONDS = 3.0
BAD_WHISPER = 'Send JSON like {"name": "Marta", "text": "the miller waters his flour"}.'
BAD_WALK = 'Send JSON like {"place": "tavern"}.'
BAD_ASK = 'Send JSON like {"villager_id": "v1", "topic": "the mill"}.'
COOKIE = "rumor_mill_player"
COOKIE_SECONDS = 365 * 24 * 3600
RELOAD = "Reload the page to join the village."
TALLY_KEYS = ("uid", "heard", "still", "frontier", "stopped", "touched", "goals")


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


def card_view(card: dict, player: Player, judge, rumors: Rumors) -> dict:
    """The card with the goals it serves for the player, each with how many it convinced, who become believers in touched.

    That is the first claim in the player's hand, and the world goal when the card makes its claim.
    """
    hand = {g.id: g for g in player.goals}
    goals = [hand[c] for c in card["claims"] if c in hand][:1]
    goals += [judge.world for c in card["claims"] if judge.world.id == f"world-{c}"]
    links = []
    for goal in goals:
        convinced = [
            vid for vid, root in judge.roots.get(goal.id, {}).items()
            if root is not None and (shown := rumors.card(root)) is not None and shown.uid == card["uid"]
        ]
        card["touched"].update(dict.fromkeys(convinced, "believes"))
        links.append({"text": goal.text, "claim": claim(goal), "world": goal is judge.world, "convinced": len(convinced)})
    return {**card, "goals": links}


def tallies(rumors: Rumors, judge, players: Players, player_id: str) -> list[dict]:
    """Each card's counts, frontier and goal links, so the live snapshot changes only when one of them does."""
    player = players.get(player_id)
    return [{k: v[k] for k in TALLY_KEYS} for v in (card_view(c, player, judge, rumors) for c in rumors.cards(player_id))]


def event_json(event: Event) -> dict:
    """The event as the page gets it; a heat event also carries its sentence."""
    data = event.to_json()
    if event.kind == "heat":
        data["text"] = HEAT_LINES[event.data["label"]]
    return data


def game_json(player: Player) -> dict:
    """The player's game state and, once it is over, only the outcome, goals and reasons."""
    s = player.summary
    summary = s and {"outcome": s.outcome, "goals": [[text, percent] for text, percent in s.goals], "reasons": s.reasons}
    return {"state": player.state, "summary": summary}


def goal_json(judge, goal: Goal, met: bool) -> dict:
    """The goal's text, whole percent and believer counts, as the page shows it."""
    believers, needed = judge.counts.get(goal.id, (None, None))
    return {"id": goal.id, "text": goal.text, "percent": int(judge.progress.get(goal.id, 0.0) * 100), "met": met,
            "believers": believers, "needed": needed}


def player_for(players: Players, cookie: str | None) -> Player | None:
    """The run of the owner the cookie names, if it names one."""
    return players.live(cookie) if cookie else None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status)


async def _payload(request: Request, bad: str, too_long: str) -> dict | JSONResponse:
    """The request's JSON object, or the 413 or 400 response for a body that is too big or not one."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return _error(413, too_long)
    try:
        payload = json.loads(body)
    except ValueError:
        return _error(400, bad)
    return payload if isinstance(payload, dict) else _error(400, bad)


def create_app(
    *, players: Players, join: Callable[[str], Player], town: Town, feed: Feed, judge, usage: Usage, port: int,
    suspicion: Suspicion, game: Game, rumors: Rumors, hints: Hints,
) -> FastAPI:
    # ponytail: unbounded per-client queues; a stalled tab on localhost is the only consumer.
    clients: dict[asyncio.Queue, str] = {}
    boot = uuid.uuid4().hex
    notes: dict[str, Notes] = {}
    # What each player's tabs were last sent, by kind.
    sent: dict[str, dict[str, object]] = {"rumors": {}, "usage": {}}

    def notes_of(player: Player) -> Notes:
        if player.id not in notes:
            notes[player.id] = Notes(feed, player.id)
        return notes[player.id]

    def broadcast(message: dict) -> None:
        for queue in clients:
            queue.put_nowait(message)

    def on_event(event: Event) -> None:
        for queue, pid in clients.items():
            if feed.sees(pid, event):
                queue.put_nowait(event_json(event))

    async def watch(kind: str, snapshot, seconds: float) -> None:
        """Send the snapshot whenever it has changed, checking every few seconds."""
        last = snapshot()
        while True:
            await asyncio.sleep(seconds)
            now = snapshot()
            if now != last:
                last = now
                broadcast({"kind": kind, kind: now})

    async def watch_each(kind: str, snapshot: Callable[[str], object], seconds: float) -> None:
        """Send each player's tabs their own snapshot whenever it has changed, checking every few seconds."""
        while True:
            await asyncio.sleep(seconds)
            for pid in set(clients.values()):
                now = snapshot(pid)
                if now != sent[kind].get(pid):
                    sent[kind][pid] = now
                    for queue in [q for q, p in clients.items() if p == pid]:
                        queue.put_nowait({"kind": kind, kind: now})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        feed.sinks.append(on_event)
        watchers = [
            asyncio.create_task(watch("villagers", lambda: villagers(town), VILLAGER_POLL_SECONDS)),
            asyncio.create_task(watch_each("usage", usage.to_json, USAGE_POLL_SECONDS)),
            asyncio.create_task(watch_each("rumors", partial(tallies, rumors, judge, players), VILLAGER_POLL_SECONDS)),
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
    async def page(request: Request):
        """Serve the page, starting a run for a new visitor or for one whose lockout has passed."""
        owner = request.cookies.get(COOKIE)
        player = player_for(players, owner)
        fresh = player is None
        if fresh:
            owner = new_owner_id()
        if fresh or (player.over() and time.time() - player.ended_at >= RUN_OUT_LOCKOUT_SECONDS):
            join(owner)
        response = FileResponse(PAGE)
        if fresh:
            response.set_cookie(COOKIE, owner, max_age=COOKIE_SECONDS, httponly=True, samesite="lax")
        return response

    @app.get("/api/state")
    async def state(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        return {
            "boot": f"{boot}:{player.id}",
            "places": list(PLACES),
            "place": player.place,
            "time": game.clock(),
            "goals": [goal_json(judge, g, g.id in player.met) for g in player.goals],
            "world_goal": goal_json(judge, judge.world, False),
            "villagers": villagers(town),
            "events": [event_json(e) for e in feed.replay(player.id)],
            "rumors": [card_view(c, player, judge, rumors) for c in rumors.cards(player.id)],
            "notes": notes_of(player).all(),
            "usage": usage.to_json(player.id),
            "heat": (label := suspicion.label(player.id)),
            "heat_line": HEAT_LINES[label],
            "game": game_json(player),
            "trophies": [t.to_json() for t in players.cases.get(player.owner, [])],
        }

    @app.get("/api/villagers/{vid}")
    async def inspect(vid: str, request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
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
            "note": notes_of(player).get(v.id),
        }

    @app.get("/api/hints")
    async def hinted(request: Request, text: str = ""):
        """The ids of villagers whose hints to the player are close to the draft."""
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        if not text.strip() or len(text) > MAX_WHISPER_CHARS:
            return {"ids": []}
        with charging(player.id):
            return {"ids": await hints.matching(player.id, text)}

    @app.post("/api/whisper")
    async def whisper(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        payload = await _payload(request, BAD_WHISPER, "That whisper is too long.")
        if isinstance(payload, JSONResponse):
            return payload
        text, vid, name = payload.get("text"), payload.get("villager_id"), payload.get("name", "")
        if not isinstance(text, str) or not isinstance(name, str) or not isinstance(vid, str | None):
            return _error(400, BAD_WHISPER)
        if problem := await (player.whisper_to(vid, text) if vid else player.whisper(name, text)):
            return _error(409 if isinstance(problem, Refusal) else 400, problem)
        return {"ok": True}

    @app.post("/api/walk")
    async def walk(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        payload = await _payload(request, BAD_WALK, "That request is too long.")
        if isinstance(payload, JSONResponse):
            return payload
        place = payload.get("place")
        if not isinstance(place, str):
            return _error(400, BAD_WALK)
        if problem := player.walk(place.strip().lower()):
            return _error(400, problem)
        return {"ok": True}

    @app.post("/api/ask")
    async def ask(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        payload = await _payload(request, BAD_ASK, "That question is too long.")
        if isinstance(payload, JSONResponse):
            return payload
        vid, topic = payload.get("villager_id"), payload.get("topic")
        if not isinstance(vid, str) or not isinstance(topic, str):
            return _error(400, BAD_ASK)
        if problem := await player.ask(vid, topic):
            return _error(409 if isinstance(problem, Refusal) else 400, problem)
        return {"ok": True}

    @app.post("/api/listen")
    async def listen(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        if problem := player.listen():
            return _error(400, problem)
        return {"ok": True}

    @app.websocket("/ws")
    async def events(socket: WebSocket):
        """Send the player's events until the client goes away; reading is what notices a closed tab."""
        await socket.accept()
        # Accepted first so the browser sees 1008, not a refused handshake; nothing is sent before closing.
        if (player := player_for(players, socket.cookies.get(COOKIE))) is None:
            await socket.close(1008)
            return
        queue: asyncio.Queue = asyncio.Queue()
        sent["rumors"].setdefault(player.id, tallies(rumors, judge, players, player.id))
        sent["usage"].setdefault(player.id, usage.to_json(player.id))
        clients[queue] = player.id

        async def pump() -> None:
            while True:
                await socket.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while (await socket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            del clients[queue]
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    return app
