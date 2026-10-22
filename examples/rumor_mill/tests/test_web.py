import json
import re
import threading
import time
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from examples.rumor_mill.config import Config
from examples.rumor_mill.contex_client import Match
from examples.rumor_mill.feed import REPLAY_EVENTS, Feed
from examples.rumor_mill.game import Game, Summary
from examples.rumor_mill.goals import Goal, Trophy, world_goal
from examples.rumor_mill.lineage import Ids, Lineage
from examples.rumor_mill.models import Utterance, Villager
from examples.rumor_mill.player import MAX_WHISPER_CHARS, Player
from examples.rumor_mill.players import RUN_OUT_LOCKOUT_SECONDS, Players, new_player_id
from examples.rumor_mill.rumors import Rumors
from examples.rumor_mill.scenes import Scene
from examples.rumor_mill.suspicion import HEAT_LINES, Suspicion
from examples.rumor_mill.town import PLACES, Town
from examples.rumor_mill.tests import helpers
from examples.rumor_mill.usage import Usage
from examples.rumor_mill import web
from examples.rumor_mill.web import create_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws"
GOAL = Goal("cat", "Half the village believes the mayor's cat is a spy", "belief")
WORLD = world_goal(Goal("well", "A third of the village believes the well is cursed", "belief"))


def receive(ws, timeout=5.0):
    """ws.receive_json with a timeout, so a missing message fails instead of hanging."""
    got = []
    reader = threading.Thread(target=lambda: got.append(ws.receive_json()), daemon=True)
    reader.start()
    reader.join(timeout)
    assert got, "no WebSocket message"
    return got[0]


class FakeEngine(helpers.FakeEngine):
    """Shows deliveries in the feed; every whisper lands."""

    def __init__(self, feed, suspicion):
        super().__init__(suspicion)
        self.feed = feed

    async def deliver(self, utterance, listener_ids):
        self.feed.on_delivery(utterance, listener_ids)
        self.contex.default = [Match(f"utt:{utterance.id}", {}, 0.9)]


@pytest.fixture
def game(monkeypatch):
    monkeypatch.setattr(web, "USAGE_POLL_SECONDS", 0.05)
    town, lineage = Town(), Lineage()
    town.add(Villager("v1", "Marta Hale", "herbalist", "anxious", "the miller", ["SECRET NEED"], {"v2": "rival"}), "well")
    town.add(Villager("v2", "Bert Cole", "miller", "jolly", "none", ["OTHER NEED"], {}), "mill")
    feed = Feed(lineage, town, out=None)
    config = Config()
    suspicion, game = Suspicion(config, town), Game()
    engine = FakeEngine(feed, suspicion)
    players = Players(feed)

    def join(owner):
        player = Player(engine, town, lineage, Ids(), feed, config, game=game, player_id=new_player_id(), owner=owner)
        players.add(player)
        player.goals = [GOAL]
        players.deliver_pending(player)
        return player

    judge = SimpleNamespace(progress={"cat": 0.42, WORLD.id: 0.1}, counts={"cat": (5, 12)}, world=WORLD, roots={})
    usage = Usage(Config())
    rumors = Rumors(helpers.StubContex(), helpers.StubBaselines(0.5), config)
    feed.sinks.append(rumors.on_event)
    app = create_app(
        players=players, join=join, town=town, feed=feed, judge=judge, usage=usage, port=PORT,
        suspicion=suspicion, game=game, rumors=rumors, hints=engine.hints,
    )
    with TestClient(app, base_url=BASE) as client:
        client.get("/")
        player = players.live(client.cookies[web.COOKIE])
        yield SimpleNamespace(
            client=client, town=town, feed=feed, app=app, usage=usage, suspicion=suspicion, player=player, config=config,
            rumors=rumors, players=players, join=join, judge=judge, engine=engine,
        )
    for p in players:
        p.close()


def as_owner(client, owner):
    """Make the client's cookie name that owner."""
    client.cookies.clear()
    client.cookies.set(web.COOKIE, owner)


def test_state_lists_goals_villagers_and_replay(game):
    game.feed.say("hello")
    state = game.client.get("/api/state").json()
    assert state["goals"] == [{"id": "cat", "text": GOAL.text, "percent": 42, "met": False, "believers": 5, "needed": 12}]
    assert state["world_goal"] == {
        "id": WORLD.id, "text": WORLD.text, "percent": 10, "met": False, "believers": None, "needed": None,
    }
    assert [v["name"] for v in state["villagers"]] == ["Bert Cole", "Marta Hale"]
    assert state["places"] == list(PLACES) and state["boot"]
    assert state["events"][-1]["text"] == "hello"
    assert state["place"] == "square"
    assert (state["time"]["day"], state["time"]["phase"]) == (1, "morning")
    assert 0 <= state["time"]["fraction"] < 0.25 and state["time"]["day_seconds"] > 0
    assert state["trophies"] == []
    assert "NEED" not in game.client.get("/api/state").text


def test_inspect_card_never_includes_needs(game):
    response = game.client.get("/api/villagers/v1")
    card = response.json()
    assert (card["occupation"], card["grudge"]) == ("herbalist", "the miller")
    assert card["relationships"] == [{"id": "v2", "name": "Bert Cole", "kind": "rival"}]
    assert "needs" not in card and "NEED" not in response.text
    assert game.client.get("/api/villagers/v9").status_code == 404


def test_state_and_card_carry_notes_without_needs_or_numbers(game):
    game.feed.on_whisper_status("w-1", "v1", "Marta Hale", "brushed_off", hint="The well keeps me up.")
    game.feed.on_whisper_status("w-2", "v2", "Bert Cole", "took_the_bait", told=3)
    notes = game.client.get("/api/state").json()["notes"]
    assert notes == {
        "v1": {"status": "brushed_off", "hints": ["The well keeps me up."], "passed": []},
        "v2": {"status": "took_the_bait", "hints": [], "passed": []},
    }
    card = game.client.get("/api/villagers/v2")
    assert card.json()["note"] == {"status": "took_the_bait", "hints": [], "passed": []}
    assert "NEED" not in card.text and "3" not in json.dumps(notes)


def test_hints_endpoint_marks_villagers_by_earned_hints(game):
    contex = game.engine.contex
    game.client.portal.call(game.engine.hints.add, game.player.id, "v1", "The vines worry me.")
    contex.results["the vines"] = [Match("hint:1", {"id": "v1", "text": "x"}, 0.9)]
    assert game.client.get("/api/hints", params={"text": "the vines"}).json() == {"ids": ["v1"]}
    assert len(contex.queries) == 1
    assert game.client.get("/api/hints", params={"text": "x" * (MAX_WHISPER_CHARS + 1)}).json() == {"ids": []}
    game.join("o-2")
    as_owner(game.client, "o-2")
    assert game.client.get("/api/hints", params={"text": "the vines"}).json() == {"ids": []}
    assert len(contex.queries) == 1
    game.client.cookies.clear()
    assert game.client.get("/api/hints", params={"text": "the vines"}).status_code == 401


def test_page_is_served(game):
    response = game.client.get("/")
    assert response.status_code == 200 and "Rumor Mill" in response.text


def test_page_has_the_trophy_case_and_world_goal(game):
    html = game.client.get("/").text
    assert 'id="trophies-toggle"' in html and 'aria-controls="trophies"' in html and 'id="world"' in html
    assert "trophy-open" in html and "innerHTML" not in html
    assert "restart the program" not in html


def test_page_has_the_walk_listen_and_ask_controls(game):
    html = game.client.get("/").text
    for needle in ("Walk here", "Listen here", "/api/walk", "/api/listen", "/api/ask", 'case "listen"', 'case "asked"'):
        assert needle in html
    assert 'class="ask"' in html or 'className: "ask"' in html
    assert "ariaDisabled" in html
    assert "innerHTML" not in html


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


def test_walk_moves_the_player_and_reports_problems(game):
    post = game.client.post
    assert post("/api/walk", json={"place": "mill"}).json() == {"ok": True}
    assert game.client.get("/api/state").json()["place"] == "mill"
    moon = post("/api/walk", json={"place": "moon"})
    assert moon.status_code == 400 and "There's no moon" in moon.json()["error"]
    bad = post("/api/walk", content=b"nope")
    assert bad.status_code == 400 and bad.json() == {"error": web.BAD_WALK}
    assert post("/api/walk", content=b"x" * 10000).status_code == 413


def test_listen_starts_and_a_second_is_refused(game):
    assert game.client.post("/api/listen").json() == {"ok": True}
    again = game.client.post("/api/listen")
    assert again.status_code == 400 and again.json() == {"error": "You're already listening."}


def test_ask_reports_problems_and_answers_through_the_feed(game):
    post = game.client.post
    post("/api/walk", json={"place": "well"})
    with game.client.websocket_connect(WS) as ws:
        assert post("/api/ask", json={"villager_id": "v1", "topic": "the mill"}).json() == {"ok": True}
        event = receive(ws)
        while event["kind"] != "asked":
            event = receive(ws)
    assert (event["villager_id"], event["line"]) == ("v1", "Marta Hale hasn't heard anything about that.")
    away = post("/api/ask", json={"villager_id": "v2", "topic": "the mill"})
    assert (away.status_code, away.json()) == (400, {"error": "Bert Cole is at the mill. Walk there to ask them."})
    bad = post("/api/ask", json={"villager_id": "v1"})
    assert (bad.status_code, bad.json()) == (400, {"error": web.BAD_ASK})
    game.suspicion.bump("v1", game.player.id, 0.9, "whispered to them directly")
    assert post("/api/ask", json={"villager_id": "v1", "topic": "the mill"}).status_code == 409


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
    whisper, = [e for e in game.feed.recent if e.kind == "whisper"]
    assert whisper.data["target_id"] == "v3"


def test_usage_is_in_state_and_reaches_the_websocket(game):
    assert game.client.get("/api/state").json()["usage"]["summary"] == "Your rumors have cost about $0.00 so far"
    with game.client.websocket_connect(WS) as ws:
        game.usage.record("haiku", 10_000, 2_000, player=game.player.id)
        event = receive(ws)
    assert event["kind"] == "usage"
    assert event["usage"]["summary"] == "Your rumors have cost about $0.02 so far"


def test_each_session_sees_only_its_own_cost(game):
    a = game.player
    game.client.cookies.clear()
    b = game.players.live(game.client.get("/").cookies[web.COOKIE])
    game.usage.record("haiku", 1_000_000, 0, player=a.id)
    game.usage.record("haiku", 1_000_000, 0)
    with game.client.websocket_connect(WS) as ws:
        assert game.client.get("/api/state").json()["usage"]["summary"] == "Your rumors have cost about $0.00 so far"
        game.usage.record("haiku", 2_000_000, 0, player=a.id)
        game.usage.record("haiku", 3_000_000, 0, player=b.id)
        event = receive(ws)
    assert event == {"kind": "usage", "usage": {"summary": "Your rumors have cost about $3.00 so far", "cost": 3.0}}
    as_owner(game.client, a.owner)
    assert game.client.get("/api/state").json()["usage"]["summary"] == "Your rumors have cost about $3.00 so far"


def test_brush_off_hint_reaches_the_websocket(game):
    hint = "Leave off, my willow bark won't gather itself."
    with game.client.websocket_connect(WS) as ws:
        game.feed.on_whisper_status("w-1", "v1", "Marta Hale", "brushed_off", hint=hint)
        event = receive(ws)
    assert (event["kind"], event["state"], event["hint"]) == ("whisper_status", "brushed_off", hint)
    assert "NEED" not in str(event)


LOSS = Summary("lost", [(GOAL.text, 42)], ["Marta Hale heard it from you directly."])


def test_state_carries_the_label_and_no_scores(game):
    state = game.client.get("/api/state").json()
    assert (state["heat"], state["heat_line"]) == ("quiet", HEAT_LINES["quiet"])
    assert state["game"] == {"state": "playing", "summary": None}
    game.suspicion.bump("v1", game.player.id, 0.11, "whispered to them directly")
    response = game.client.get("/api/state")
    assert (response.json()["heat"], response.json()["heat_line"]) == ("watched", HEAT_LINES["watched"])
    assert "0.11" not in response.text and "0.055" not in response.text


def test_heat_event_carries_its_sentence(game):
    with game.client.websocket_connect(WS) as ws:
        game.feed.on_heat("watched")
        event = receive(ws)
    assert (event["kind"], event["label"], event["text"]) == ("heat", "watched", HEAT_LINES["watched"])
    assert game.client.get("/api/state").json()["events"][-1]["text"] == HEAT_LINES["watched"]


def test_gameover_event_reaches_the_websocket(game):
    with game.client.websocket_connect(WS) as ws:
        game.player.end(LOSS)
        event = receive(ws)
    assert event["kind"] == "gameover"
    assert (event["outcome"], event["goals"], event["reasons"]) == ("lost", [[GOAL.text, 42]], LOSS.reasons)


def test_state_after_loss_carries_the_summary(game):
    game.player.end(LOSS)
    state = game.client.get("/api/state").json()
    assert state["game"] == {
        "state": "lost",
        "summary": {"outcome": "lost", "goals": [[GOAL.text, 42]], "reasons": LOSS.reasons},
    }
    assert state["events"][-1]["kind"] == "gameover"


def test_nothing_reaches_the_page_after_the_game_is_over(game):
    with game.client.websocket_connect(WS) as ws:
        game.player.end(LOSS)
        assert receive(ws)["kind"] == "gameover"
        game.feed.on_time(2, "morning")
        game.usage.record("haiku", 10_000, 2_000, player=game.player.id)
        assert receive(ws)["kind"] == "usage"
    assert game.client.get("/api/state").json()["events"][-1]["kind"] == "gameover"


def test_whisper_after_game_over_is_refused(game):
    game.player.end(LOSS)
    response = game.client.post("/api/whisper", json={"villager_id": "v1", "text": "hi"})
    assert (response.status_code, response.json()) == (400, {"error": "The game is over."})


def test_a_refused_whisper_is_an_error_with_the_sentence_and_nothing_else(game):
    game.suspicion.bump("v1", game.player.id, game.config.refuse_threshold, "whispered to them directly")
    with game.client.websocket_connect(WS) as ws:
        response = game.client.post("/api/whisper", json={"villager_id": "v1", "text": "hi"})
        event = receive(ws)
    assert (response.status_code, response.json()) == (409, {"error": "Marta Hale turns away from you."})
    assert (event["kind"], event["name"], event["line"]) == ("refusal", "Marta Hale", None)


SUSPICION_KEYS = ("susp", "score", "level", "evidence", "amount", "heat", "game", "summary", "reasons", "label")


def walk(value, key=""):
    """Yield (key, value) for every leaf, keyed by the nearest dict key."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v, key)
    else:
        yield key, value


def test_no_api_response_contains_a_suspicion_number_or_villager_id_for_suspects(game):
    scores = {"v1": 0.73, "v2": 0.21}
    for vid, amount in scores.items():
        game.suspicion.bump(vid, game.player.id, amount, "heard the newcomer named")
    responses = []
    with game.client.websocket_connect(WS) as ws:
        responses.append(game.client.post("/api/whisper", json={"villager_id": "v2", "text": "the well is cursed"}).json())
        responses.append(game.client.post("/api/whisper", json={"villager_id": "v1", "text": "the mill is cursed"}).json())
        game.feed.on_warning("v2", "Bert Cole", "v1", "Marta Hale", game.player.id)
        game.feed.on_heat(game.suspicion.label(game.player.id), player=game.player.id)
        game.player.end(Summary("lost", [(GOAL.text, 42)], game.suspicion.reasons(game.player.id)))
        events = [receive(ws) for _ in range(8)]
    kinds = ["whisper", "place", "whisper_status", "whisper_status", "refusal", "warning", "heat", "gameover"]
    assert [e["kind"] for e in events] == kinds
    assert events[-1]["reasons"][:2] == ["Marta Hale grew wary of the rumors going around.", "Bert Cole grew wary of the rumors going around."]
    responses += [game.client.get("/api/state").json(), *(game.client.get(f"/api/villagers/{v}").json() for v in scores)]
    responses += [game.client.post("/api/whisper", json={"villager_id": "v1", "text": "hi"}).json(), *events]
    raw = {str(game.suspicion.score(v, game.player.id)) for v in scores} | {str(a) for a in scores.values()}
    raw |= {str(game.suspicion.level(game.player.id)), str(game.config.refuse_threshold)}
    for response in responses:
        text = re.sub(r'"at": [0-9.e+-]+', '', json.dumps(response))
        assert not any(r in text for r in raw), text
        for key, value in walk(response):
            if any(word in key for word in SUSPICION_KEYS):
                assert not isinstance(value, int | float) or isinstance(value, bool) or key == "goals", (key, value)
                assert value not in scores, (key, value)


def test_scene_event_reaches_the_websocket(game):
    scene = Scene("s-1", "well", "w-1", "w-1", False, "u-1", {"v1", "v2"}, {"v1", "v2"}, ["u-1", "u-2"], 0.73)
    with game.client.websocket_connect(WS) as ws:
        game.feed.on_scene(scene)
        event = receive(ws)
    assert (event["kind"], event["tone"], event["people"], event["lines"]) == ("scene", "conversation", 2, 2)
    assert "0.73" not in json.dumps(event)
    for key, value in walk(event):
        if any(word in key for word in SUSPICION_KEYS):
            assert not isinstance(value, int | float) or isinstance(value, bool), (key, value)


def test_state_carries_cards_beyond_the_replay_window(game):
    game.client.portal.call(partial(
        game.rumors.add, "w-1", "the miller waters his flour", datetime.now(UTC), "v1", "Marta Hale", player=game.player.id,
    ))
    hop = Utterance("u-1", "v1", "Marta Hale", "well", "the miller cheats", "w-1")
    game.rumors.record("w-1", hop, {"v2": "Bert Cole"}, True)
    for i in range(REPLAY_EVENTS + 1):
        game.feed.say(f"line {i}")
    card, = game.client.get("/api/state").json()["rumors"]
    assert (card["uid"], card["target_name"], card["heard"], card["still"]) == ("w-1", "Marta Hale", 2, 1)
    assert [h["uid"] for h in card["hops"]] == ["u-1"]


def test_card_tallies_reach_the_websocket(game):
    with game.client.websocket_connect(WS) as ws:
        game.client.portal.call(partial(
        game.rumors.add, "w-1", "the miller waters his flour", datetime.now(UTC), "v1", "Marta Hale", player=game.player.id,
    ))
        event = receive(ws)
        while event["kind"] != "rumors":
            event = receive(ws)
    assert event["rumors"] == [
        {"uid": "w-1", "heard": 1, "still": 0, "frontier": [], "stopped": None, "touched": {"v1": "heard"},
         "goals": []},
    ]


def card_with_claims(game, claims):
    game.client.portal.call(partial(
        game.rumors.add, "w-1", "the cat spies by the well", datetime.now(UTC), "v1", "Marta Hale", player=game.player.id,
    ))
    game.rumors.card("w-1").claims = claims


def test_cards_link_goals_and_count_who_each_convinced(game):
    card_with_claims(game, ["cat", "well"])
    game.judge.roots = {"cat": {"v1": "w-1", "v2": None}, WORLD.id: {"v2": "w-1"}}
    card, = game.client.get("/api/state").json()["rumors"]
    assert card["goals"] == [
        {"text": GOAL.text, "claim": "the mayor's cat is a spy", "world": False, "convinced": 1},
        {"text": WORLD.text, "claim": "the well is cursed", "world": True, "convinced": 1},
    ]
    assert card["touched"] == {"v1": "believes", "v2": "believes"}


def test_another_players_believers_never_count_on_your_card(game):
    card_with_claims(game, ["cat"])
    game.client.portal.call(partial(
        game.rumors.add, "w-9", "the cat is a spy", datetime.now(UTC), "v2", "Bert Cole", player="p-other",
    ))
    game.judge.roots = {"cat": {"v1": "w-9"}}
    card, = game.client.get("/api/state").json()["rumors"]
    assert card["goals"][0]["convinced"] == 0 and card["touched"] == {"v1": "heard"}


def test_first_visit_sets_an_owner_cookie(game):
    game.client.cookies.clear()
    response = game.client.get("/")
    assert response.cookies[web.COOKIE].startswith("o-")
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "max-age=31536000" in header


def test_a_new_run_keeps_the_same_person_and_trophy_case(game):
    owner = game.client.cookies[web.COOKIE]
    game.players.cases[owner] = [Trophy("personal", ("a", "b", "c"), 2)]
    game.player.end(LOSS)
    game.player.ended_at -= RUN_OUT_LOCKOUT_SECONDS
    game.client.get("/")
    body = game.client.get("/api/state").json()
    assert game.client.cookies[web.COOKIE] == owner and body["trophies"] == [{"kind": "personal", "goals": ["a", "b", "c"], "day": 2}]


def test_two_sessions_play_as_two_players(game):
    a = game.client.cookies[web.COOKIE]
    game.client.post("/api/whisper", json={"villager_id": "v1", "text": "the well is cursed"})
    game.client.cookies.clear()
    b = game.client.get("/").cookies[web.COOKIE]
    state = game.client.get("/api/state")
    body = state.json()
    assert a != b and a not in state.text
    assert body["rumors"] == [] and body["notes"] == {} and not [e for e in body["events"] if e["kind"] == "whisper"]
    assert body["world_goal"]["text"] == game.judge.world.text


def test_a_players_socket_gets_none_of_another_players_events(game):
    a = game.client.cookies[web.COOKIE]
    game.client.cookies.clear()
    b = game.players.live(game.client.get("/").cookies[web.COOKIE])
    with game.client.websocket_connect(WS) as ws:
        as_owner(game.client, a)
        assert game.client.post("/api/whisper", json={"villager_id": "v1", "text": "the well is cursed"}).json() == {"ok": True}
        game.feed.say("ping", player=b.id)
        assert receive(ws)["text"] == "ping"


def test_two_tabs_of_one_player_both_get_its_events(game):
    with game.client.websocket_connect(WS) as ws1, game.client.websocket_connect(WS) as ws2:
        game.client.post("/api/whisper", json={"villager_id": "v1", "text": "the well is cursed"})
        assert receive(ws1)["kind"] == receive(ws2)["kind"] == "whisper"


def test_an_unknown_cookie_asks_for_a_reload(game):
    as_owner(game.client, "p-forged")
    response = game.client.get("/api/state")
    assert (response.status_code, response.json()) == (401, {"error": web.RELOAD})
    assert game.client.get("/api/villagers/v1").status_code == 401
    assert game.client.post("/api/whisper", json={"villager_id": "v1", "text": "hi"}).status_code == 401
    with game.client.websocket_connect(WS) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008
    assert game.client.get("/").cookies[web.COOKIE] != "p-forged"


def test_a_run_out_player_starts_over_only_after_the_lockout(game):
    game.player.end(LOSS)
    assert web.COOKIE not in game.client.get("/").cookies
    assert game.players.live(game.player.owner) is game.player
    game.player.ended_at -= RUN_OUT_LOCKOUT_SECONDS
    game.client.get("/")
    assert game.players.live(game.player.owner).id != game.player.id


def test_a_new_run_resets_the_page(game):
    boot = game.client.get("/api/state").json()["boot"]
    game.player.end(LOSS)
    game.player.ended_at -= RUN_OUT_LOCKOUT_SECONDS
    game.client.get("/")
    assert game.client.get("/api/state").json()["boot"] != boot


def test_state_never_names_or_counts_other_players(game):
    other = game.join("o-other")
    game.feed.say("for the other one", player=other.id)
    response = game.client.get("/api/state")
    assert other.id not in response.text and "o-other" not in response.text and "for the other one" not in response.text
    assert "players" not in response.json()
