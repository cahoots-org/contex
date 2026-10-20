import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from examples.rumor_mill.config import Config
from examples.rumor_mill.feed import Feed
from examples.rumor_mill.goals import Goal
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Villager
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Player
from examples.rumor_mill.town import PLACES, Town
from examples.rumor_mill.usage import Usage
from examples.rumor_mill import web
from examples.rumor_mill.web import create_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws"
GOAL = Goal("cat", "Half the village believes the mayor's cat is a spy", "belief")


def receive(ws, timeout=5.0):
    """ws.receive_json with a timeout, so a missing message fails instead of hanging."""
    got = []
    reader = threading.Thread(target=lambda: got.append(ws.receive_json()), daemon=True)
    reader.start()
    reader.join(timeout)
    assert got, "no WebSocket message"
    return got[0]


class FakeEngine:
    def __init__(self, feed):
        self.feed = feed

    async def deliver(self, utterance, listener_ids):
        self.feed.on_delivery(utterance, listener_ids)


@pytest.fixture
def game(monkeypatch):
    monkeypatch.setattr(web, "USAGE_POLL_SECONDS", 0.05)
    town, lineage = Town(), Lineage()
    town.add(Villager("v1", "Marta Hale", "herbalist", "anxious", "the miller", ["SECRET NEED"], {"v2": "rival"}), "well")
    town.add(Villager("v2", "Bert Cole", "miller", "jolly", "none", ["OTHER NEED"], {}), "mill")
    feed = Feed(lineage, town, out=None)
    player = Player(FakeEngine(feed), town, lineage, Ids(), feed, Config(shrug_seconds=60))
    judge = SimpleNamespace(progress={"cat": 0.42}, met=set())
    usage = Usage(Config())
    app = create_app(player=player, town=town, feed=feed, judge=judge, goals=[GOAL], usage=usage, port=PORT)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, town=town, feed=feed, app=app, usage=usage)
    player.close()


def test_state_lists_goals_villagers_and_replay(game):
    game.feed.say("hello")
    state = game.client.get("/api/state").json()
    assert state["goals"] == [{"id": "cat", "text": GOAL.text, "percent": 42, "met": False}]
    assert [v["name"] for v in state["villagers"]] == ["Bert Cole", "Marta Hale"]
    assert state["places"] == list(PLACES) and state["boot"]
    assert state["events"][-1]["text"] == "hello"
    assert "NEED" not in game.client.get("/api/state").text


def test_inspect_card_never_includes_needs(game):
    response = game.client.get("/api/villagers/v1")
    card = response.json()
    assert (card["occupation"], card["grudge"]) == ("herbalist", "the miller")
    assert card["relationships"] == [{"id": "v2", "name": "Bert Cole", "kind": "rival"}]
    assert "needs" not in card and "NEED" not in response.text
    assert game.client.get("/api/villagers/v9").status_code == 404


def test_page_is_served(game):
    response = game.client.get("/")
    assert response.status_code == 200 and "Rumor Mill" in response.text


def test_whisper_validation_errors_come_back_as_messages(game):
    post = game.client.post
    assert post("/api/whisper", json={"name": "Zed", "text": "hi"}).json() == {"error": "No one in town is called Zed."}
    too_long = post("/api/whisper", json={"villager_id": "v1", "text": "x" * (MAX_WHISPER_CHARS + 1)})
    assert too_long.status_code == 400 and "500" in too_long.json()["error"]
    assert post("/api/whisper", content=b"not json").status_code == 400
    assert post("/api/whisper", json=["text"]).status_code == 400
    assert post("/api/whisper", json={"name": 3, "text": "hi"}).status_code == 400
    assert post("/api/whisper", content=b"x" * 10000).status_code == 413
    game.town.remove("v2")
    assert post("/api/whisper", json={"villager_id": "v2", "text": "hi"}).json() == {"error": "Bert Cole has left town."}


def test_websocket_receives_the_whisper(game):
    with game.client.websocket_connect(WS) as ws:
        response = game.client.post("/api/whisper", json={"villager_id": "v1", "text": "the well is cursed"})
        assert response.json() == {"ok": True}
        event = receive(ws)
    assert (event["kind"], event["target"], event["text"]) == ("whisper", "Marta Hale", "the well is cursed")


def test_villager_moves_reach_the_websocket(game):
    with game.client.websocket_connect(WS) as ws:
        game.town.move("v1", "tavern")
        event = receive(ws)
    assert event["kind"] == "villagers"
    assert {v["name"]: v["place"] for v in event["villagers"]}["Marta Hale"] == "tavern"


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1:9999", "localhost"])
def test_other_hosts_are_refused(game, host):
    assert game.client.get("/api/state", headers={"host": host}).status_code == 403


def test_cross_origin_writes_are_refused(game):
    post = game.client.post
    body = {"villager_id": "v1", "text": "hi"}
    assert post("/api/whisper", json=body, headers={"origin": "http://evil.example"}).status_code == 403
    assert post("/api/whisper", json=body, headers={"origin": f"http://localhost:{PORT}"}).status_code == 403
    assert post("/api/whisper", json=body, headers={"origin": BASE}).status_code == 200
    assert post("/api/whisper", json=body, headers={"origin": "null"}).status_code == 403
    for headers in ({"origin": "http://evil.example"}, {"origin": "null"}, {"host": "evil.example:8765"}):
        with pytest.raises(WebSocketDisconnect):
            with game.client.websocket_connect(WS, headers=headers) as ws:
                receive(ws)


def test_same_origin_websocket_is_accepted(game):
    with game.client.websocket_connect(WS, headers={"origin": BASE}) as ws:
        game.feed.say("hello")
        assert receive(ws)["text"] == "hello"


def test_closed_tab_is_dropped(game):
    with game.client.websocket_connect(WS):
        assert len(game.app.state.clients) == 1
    deadline = time.monotonic() + 5
    while game.app.state.clients and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not game.app.state.clients


def test_whisper_by_id_picks_the_right_namesake(game):
    game.town.add(Villager("v3", "Bert Cole", "baker", "shy", "none", [], {}), "square")
    assert game.client.post("/api/whisper", json={"name": "Bert Cole", "text": "hi"}).status_code == 400
    assert game.client.post("/api/whisper", json={"villager_id": "v3", "text": "hi"}).json() == {"ok": True}
    assert game.feed.recent[-1].data["target_id"] == "v3"


def test_usage_is_in_state_and_reaches_the_websocket(game):
    assert game.client.get("/api/state").json()["usage"]["summary"] == "Estimated cost so far: $0.00"
    with game.client.websocket_connect(WS) as ws:
        game.usage.record("haiku", 10_000, 2_000)
        event = receive(ws)
    assert event["kind"] == "usage"
    assert event["usage"]["summary"] == "Estimated cost so far: $0.02 (Haiku 10k in / 2k out)"


def test_shrug_hint_reaches_the_websocket(game):
    with game.client.websocket_connect(WS) as ws:
        game.feed.on_shrug("v1", "Marta Hale", "w-1", "Leave off, my willow bark won't gather itself.")
        event = receive(ws)
    assert (event["kind"], event["hint"]) == ("shrug", "Leave off, my willow bark won't gather itself.")
    assert "NEED" not in str(event)
