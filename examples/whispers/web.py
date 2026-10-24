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

from examples.whispers.feed import RESTING, Event, Feed
from examples.whispers.game import Game
from examples.whispers.goals import Goal, claim, goal_counts, goal_targets
from examples.whispers.hints import Hints
from examples.whispers.notes import Notes
from examples.whispers.player import MAX_WHISPER_CHARS, Player, Refusal
from examples.whispers.players import RUN_OUT_LOCKOUT_SECONDS, Players, new_owner_id
from examples.whispers.rumors import Rumors
from examples.whispers.suspicion import HEAT_LINES, Suspicion, influence
from examples.whispers.town import PLACES, Town
from examples.whispers.usage import Usage, charging

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "index.html"
MAX_BODY_BYTES = 8192
VILLAGER_POLL_SECONDS = 1.0
USAGE_POLL_SECONDS = 3.0
# How many messages a tab may fall behind before its socket is closed.
CLIENT_QUEUE = 256
BAD_WHISPER = 'Send JSON like {"name": "Marta", "text": "the miller waters his flour"}.'
BAD_WALK = 'Send JSON like {"place": "tavern"}.'
BAD_SWAP = 'Send JSON like {"goal_id": "g1"}.'
BAD_ASK = 'Send JSON like {"villager_id": "v1", "topic": "the mill"}.'
COOKIE = "whispers_player"
COOKIE_SECONDS = 365 * 24 * 3600
RELOAD = "Reload the page to join the village."
TALLY_KEYS = ("uid", "heard", "frontier", "stopped", "touched", "goals")
LOCAL_HOSTS = ("127.0.0.1", "localhost")


class Guard:
    """Refuse writes from other origins and, on a local server, requests for other hosts (DNS rebinding)."""

    def __init__(self, app, port: int, host: str = "127.0.0.1"):
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"} if host in LOCAL_HOSTS else None

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            origin = headers.get("origin")
            writes = scope["type"] == "websocket" or scope["method"] not in ("GET", "HEAD")
            foreign = self.hosts is not None and host not in self.hosts
            if foreign or (writes and origin is not None and origin not in (f"http://{host}", f"https://{host}")):
                if scope["type"] == "websocket":
                    await WebSocketClose(1008)(scope, receive, send)
                else:
                    await PlainTextResponse("Forbidden", 403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def villagers(town: Town) -> list[dict]:
    return sorted(
        ({"id": v.id, "name": v.name, "occupation": v.occupation, "place": town.place_of(v.id),
          "influence": influence(v)}
         for v in town.villagers),
        key=lambda v: v["name"],
    )


def card_view(card: dict, player: Player, judge, rumors: Rumors) -> dict:
    """The card with the goals it serves for the player, each with how many it convinced, who become believers in touched.

    That is the first claim in the player's hand, and the world goal when the card makes its claim.
    """
    claims = rumors.card(card["uid"]).claims
    hand = {g.id: g for g in player.goals}
    goals = [hand[c] for c in claims if c in hand][:1]
    if judge.world.id in claims:
        goals.append(judge.world)
    touched = dict(card["touched"])
    links = []
    for goal in goals:
        convinced = [
            vid for vid, root in judge.roots.get(goal.id, {}).items()
            if root is not None and (shown := rumors.card(root)) is not None and shown.uid == card["uid"]
        ]
        touched.update(dict.fromkeys(convinced, "believes"))
        links.append({"text": goal.text, "claim": claim(goal), "world": goal is judge.world, "convinced": len(convinced)})
    return {**card, "touched": touched, "goals": links}


def beliefs(judge, player: Player) -> dict[str, list[str]]:
    """Each villager's claims among the player's goals, then the world goal, that they believe."""
    found: dict[str, list[str]] = {}
    for goal in [*player.goals, judge.world]:
        for vid in judge.roots.get(goal.id, {}):
            if text := claim(goal):
                found.setdefault(vid, []).append(text)
    return found


def beliefs_of(judge, players: Players, player_id: str) -> dict[str, list[str]]:
    """The beliefs of the player with this id."""
    return beliefs(judge, players.get(player_id))


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


def goal_json(judge, goal: Goal, met: bool, villagers: int) -> dict:
    """The goal's text, whole percent, believer counts and doubt, as the page shows it."""
    believers, needed = goal_counts(goal, judge.counts, villagers) or (None, None)
    return {"id": goal.id, "text": goal.text, "percent": int(judge.progress.get(goal.id, 0.0) * 100), "met": met,
            "believers": believers, "needed": needed, "doubt": judge.doubt.get(goal.id), "standing": judge.standing(goal),
            **goal_targets(goal)}


def offer(clients: dict[asyncio.Queue, str], queue: asyncio.Queue, message: dict) -> None:
    """Queue the message for a tab; a tab too far behind stops getting messages and is told to close."""
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        clients.pop(queue, None)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)


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
    except (ValueError, RecursionError):
        return _error(400, bad)
    return payload if isinstance(payload, dict) else _error(400, bad)


def create_app(
    *, players: Players, join: Callable[[str], Player], town: Town, feed: Feed, judge, usage: Usage, port: int,
    suspicion: Suspicion, game: Game, rumors: Rumors, hints: Hints, host: str = "127.0.0.1",
) -> FastAPI:
    clients: dict[asyncio.Queue, str] = {}
    boot = uuid.uuid4().hex
    notes: dict[str, Notes] = {}
    # What each player's tabs were last sent, by kind.
    sent: dict[str, dict[str, object]] = {"rumors": {}, "usage": {}, "beliefs": {}}

    def notes_of(player: Player) -> Notes:
        if player.id not in notes:
            notes[player.id] = Notes(feed, player.id)
        return notes[player.id]

    def broadcast(message: dict) -> None:
        for queue in list(clients):
            offer(clients, queue, message)

    def on_event(event: Event) -> None:
        for queue, pid in list(clients.items()):
            if feed.sees(pid, event):
                offer(clients, queue, event_json(event))

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
                        offer(clients, queue, {"kind": kind, kind: now})

    async def watch_rest() -> None:
        """Tell everyone when the village starts resting for the day and when it wakes."""
        resting = usage.resting()
        while True:
            await asyncio.sleep(USAGE_POLL_SECONDS)
            if usage.resting() != resting:
                resting = not resting
                feed.on_rest(resting)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        feed.sinks.append(on_event)
        watchers = [
            asyncio.create_task(watch("villagers", lambda: villagers(town), VILLAGER_POLL_SECONDS)),
            asyncio.create_task(watch_each("usage", usage.to_json, USAGE_POLL_SECONDS)),
            asyncio.create_task(watch_each("rumors", partial(tallies, rumors, judge, players), VILLAGER_POLL_SECONDS)),
            asyncio.create_task(watch_each("beliefs", partial(beliefs_of, judge, players), VILLAGER_POLL_SECONDS)),
            asyncio.create_task(watch_rest()),
        ]
        try:
            yield
        finally:
            feed.sinks.remove(on_event)
            for w in watchers:
                w.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, port=port, host=host)
    app.state.clients = clients

    @app.get("/healthz")
    async def healthz():
        return PlainTextResponse("ok")

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
            response.set_cookie(
                COOKIE, owner, max_age=COOKIE_SECONDS, httponly=True, samesite="lax", secure=request.url.scheme == "https",
            )
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
            "talk_hold_seconds": player.config.talk_hold_seconds,
            "goals": [goal_json(judge, g, g.id in player.met, len(town.villagers)) for g in player.goals],
            "world_goal": goal_json(judge, judge.world, False, len(town.villagers)),
            "villagers": villagers(town),
            "events": [event_json(e) for e in feed.replay(player.id)],
            "rumors": [card_view(c, player, judge, rumors) for c in rumors.cards(player.id)],
            "notes": notes_of(player).all(),
            "usage": usage.to_json(player.id),
            "beliefs": beliefs(judge, player),
            "heat": (label := suspicion.label(player.id)),
            "heat_line": HEAT_LINES[label],
            "game": game_json(player),
            "trophies": [t.to_json() for t in players.cases.get(player.owner, [])],
            "resting": usage.resting(),
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
            "influence": influence(v),
            "believes": beliefs(judge, player).get(v.id, []),
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
        if usage.resting():
            return _error(409, RESTING)
        if problem := await (player.whisper_to(vid, text) if vid else player.whisper(name, text)):
            return _error(409 if isinstance(problem, Refusal) else 400, problem)
        return {"ok": True}

    @app.post("/api/goal/swap")
    async def swap_goal(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        payload = await _payload(request, BAD_SWAP, "That request is too long.")
        if isinstance(payload, JSONResponse):
            return payload
        goal_id = payload.get("goal_id")
        if not isinstance(goal_id, str):
            return _error(400, BAD_SWAP)
        if usage.resting():
            return _error(409, RESTING)
        player.touch()
        if problem := await judge.swap(player, goal_id):
            return _error(409, problem)
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
        if usage.resting():
            return _error(409, RESTING)
        if problem := await player.ask(vid, topic):
            return _error(409 if isinstance(problem, Refusal) else 400, problem)
        return {"ok": True}

    @app.post("/api/typing")
    async def typing(request: Request):
        if (player := player_for(players, request.cookies.get(COOKIE))) is None:
            return _error(401, RELOAD)
        player.typing()
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
        queue: asyncio.Queue = asyncio.Queue(CLIENT_QUEUE)
        sent["rumors"].setdefault(player.id, tallies(rumors, judge, players, player.id))
        sent["usage"].setdefault(player.id, usage.to_json(player.id))
        sent["beliefs"].setdefault(player.id, beliefs(judge, player))
        clients[queue] = player.id
        player.connected()

        async def pump() -> None:
            while (message := await queue.get()) is not None:
                await socket.send_json(message)
            await socket.close(1013)

        sender = asyncio.create_task(pump())
        try:
            while (await socket.receive())["type"] != "websocket.disconnect":
                pass
        finally:
            clients.pop(queue, None)
            player.disconnected()
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    return app
