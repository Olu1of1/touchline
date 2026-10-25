"""Tests for the Touchline agents. Run: python -m pytest -q"""

import pytest

from touchline import winprob
from touchline.describe import zone, shot_spot, minute_phrase, nth
from touchline.pipeline import build
from touchline.verify import verify

NAMES = {"Marsden", "Dufour", "Eskmouth", "Dunhallow", "Strachan"}


@pytest.fixture(scope="module")
def m059():
    return build("M059", backend=None)


def test_every_goal_announced_fast(m059):
    m = m059["metrics"]
    assert m["goals_announced"] == m["goals"] > 0
    assert m["goal_latency_max_s"] < 1.0


def test_almost_every_shot_described(m059):
    m = m059["metrics"]
    assert m["shots_narrated"] >= 0.9 * m["shots"]


def test_one_voice_at_a_time(m059):
    tl = sorted(m059["timeline"], key=lambda u: u["start"])
    for a, b in zip(tl, tl[1:]):
        assert b["start"] >= (a["cut_at"] or a["end"]) - 0.01, (a["text"], b["text"])


def test_nothing_said_before_it_happened(m059):
    for u in m059["timeline"]:
        assert u["start"] >= u["born"] - 0.01


def test_causal(m059):
    """Removing everything after minute ~40 must not change what was said up to minute ~39."""
    cut = 45 + 40 * 60
    part = build("M059", backend=None, until=cut)
    horizon = cut - 30
    full = [(u["start"], u["text"]) for u in m059["timeline"] if u["start"] < horizon]
    trunc = [(u["start"], u["text"]) for u in part["timeline"] if u["start"] < horizon]
    assert full == trunc


def test_scoreboard_matches_result(m059):
    last = m059["snapshots"][-1]
    assert last["score"] == m059["match"]["final_score"]


def test_verifier_catches_invented_numbers_and_names():
    facts = {"home": {"name": "Eskmouth", "goals": 2}, "away": {"name": "Dunhallow", "goals": 1},
             "goals": [{"scorer": "Marsden", "minute": 24}], "stats": {"shots": [4, 13]}}
    ok, _ = verify("Marsden scored in the 24th minute, and Dunhallow have had 13 shots.", facts, NAMES)
    assert ok
    ok, probs = verify("Marsden has scored 14 goals this season.", facts, NAMES)
    assert not ok and any("14" in p for p in probs)
    ok, probs = verify("Strachan has been their best player.", facts, NAMES)
    assert not ok and any("Strachan" in p for p in probs)
    ok, probs = verify("That's his fifth of the season.", facts, NAMES)
    assert not ok


def test_colour_lines_all_pass_verifier(m059):
    for c in m059["colour_log"]:
        if c["final"]:
            assert c.get("template_check", "passed") == "passed" or c.get("verdict") == "accepted"


def test_win_probability_sane():
    h, d, a = winprob.probabilities(1.6, 1.2, 0, 0, 0)
    assert abs(h + d + a - 1) < 1e-9 and h > a
    h2, _, _ = winprob.probabilities(1.6, 1.2, 1, 0, 85 * 60)
    assert h2 > 0.85                    # 1-0 up late on: very likely to win
    h3, d3, a3 = winprob.probabilities(1.6, 1.2, 2, 2, 96 * 60)
    assert d3 > 0.99                    # level at the final whistle


def test_language():
    assert zone(96, 34) == "inside the box"
    assert zone(101, 34) == "in the six-yard box"
    assert "right side side" not in zone(92, 20)
    assert shot_spot(94, 34).startswith("12 yards out")
    assert minute_phrase("1'") == "1 minute"
    assert minute_phrase("45+2'") == "two minutes into added time"
    assert [nth(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th",
                                                                    "21st", "22nd"]


class FakeLLM:
    """Stands in for Azure OpenAI: alternates a faithful line with one that invents a number and a player."""
    name = "fake-llm"

    def __init__(self):
        self.n = 0

    def colour(self, trigger, facts, recently_said):
        self.n += 1
        if self.n % 2:
            return f"{facts['home']['name']} {facts['home']['goals']}, {facts['away']['name']} {facts['away']['goals']}.", 0.8
        return "That's his 37th goal for the club, and Pemberton is warming up.", 0.8


def test_llm_lines_go_through_the_verifier():
    tl = build("M059", backend=FakeLLM())
    log = [c for c in tl["colour_log"] if "proposed" in c]
    assert log
    rejected = [c for c in log if c["verdict"] == "rejected"]
    accepted = [c for c in log if c["verdict"] == "accepted"]
    assert rejected and accepted
    spoken = {u["text"] for u in tl["timeline"]}
    assert not any("37th" in t or "Pemberton" in t for t in spoken)
    # model latency is respected: a colour line is never spoken before the model could have answered
    for u in tl["timeline"]:
        if u["source"] == "colour" and u["meta"].get("source") == "fake-llm":
            assert u["start"] >= u["born"] >= 0


def test_ssml_is_valid_xml():
    import xml.etree.ElementTree as ET
    from touchline.tts import ssml
    ET.fromstring(ssml("Goal! Marsden & co. <score> 2-1", excite=2.0, rate=1.2))


# ------------------------------------------------- Agent Framework agents
from agent_framework import BaseChatClient, ChatResponse, Message, Content
from agent_framework._tools import FunctionInvocationLayer


class ScriptedClient(FunctionInvocationLayer, BaseChatClient):
    """A chat client that plays back a script: tool calls first, then text. Stands in for Azure OpenAI."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.prompts = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.prompts.append(" ".join(m.text or "" for m in messages if m.role == "user"))
        kind, *rest = self.script.pop(0)

        async def respond():
            if kind == "call":
                name, args = rest
                return ChatResponse(messages=[Message(role="assistant", contents=[
                    Content.from_function_call(call_id=f"c{len(self.script)}", name=name, arguments=args)])])
            return ChatResponse(messages=[Message(role="assistant", contents=[rest[0]])])
        return respond()


T26 = 45 + 26 * 60          # broadcast time around the 26th minute of M059 (Eskmouth 2-1 Dunhallow)


def test_ask_uses_tools_and_is_accepted():
    from touchline.agents import AskTouchline
    client = ScriptedClient([("call", "team_stats", {"team": "Dunhallow"}),
                             ("text", "Dunhallow have had 10 shots, 3 of them on target.")])
    r = AskTouchline(client=client).ask("M059", T26, "How many shots have Dunhallow had?")
    assert r["verdict"] == "accepted" and r["attempts"] == 1
    assert r["tools_used"] == ["team_stats"]
    assert "10 shots" in r["answer"]


def test_ask_retries_after_verifier_rejects():
    from touchline.agents import AskTouchline
    client = ScriptedClient([("call", "goals_so_far", {}),
                             ("text", "Marsden has scored twice, his 14th and 15th of the season."),
                             ("text", "Marsden has scored twice, in the 1st and 24th minutes.")])
    r = AskTouchline(client=client).ask("M059", T26, "Has Marsden scored?")
    assert r["verdict"] == "accepted" and r["attempts"] == 2
    assert "14th" not in r["answer"]
    assert "rejected" in client.prompts[-1] and "14" in client.prompts[-1]     # the agent was told why


def test_ask_falls_back_when_model_keeps_inventing():
    from touchline.agents import AskTouchline
    client = ScriptedClient([("text", "Pemberton has 9 goals."), ("text", "Pemberton has 9 goals.")])
    r = AskTouchline(client=client).ask("M059", T26, "Who's their top scorer?")
    assert r["verdict"] == "rejected_fallback"
    assert "Pemberton" not in r["answer"]


def test_ask_cannot_see_the_future():
    from touchline.agents import LiveView, MatchTools
    import json
    early = json.loads(MatchTools(LiveView("M059", T26)).goals_so_far())
    late = json.loads(MatchTools(LiveView("M059", 45 + 50 + 95 * 60)).goals_so_far())
    assert early["count"] == 3 and late["count"] == 7


def test_colour_agent_runs_through_agent_framework():
    from touchline.agents import AgentFrameworkColour
    script = []
    for _ in range(400):
        script += [("text", "Shots so far: 777 apiece, which nobody said."), ("text", "SKIP")]
    tl = build("M059", backend=AgentFrameworkColour(client=ScriptedClient(script)))
    log = [c for c in tl["colour_log"] if "attempts" in c]
    assert log and all(c["attempts"] == 2 for c in log)
    spoken = " ".join(u["text"] for u in tl["timeline"])
    assert "777" not in spoken


class BrokenClient(ScriptedClient):
    def __init__(self):
        super().__init__([])

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def fail():
            raise RuntimeError("401 Unauthorized: wrong key")
        return fail()


def test_model_errors_fall_back_to_templates():
    from touchline.agents import AgentFrameworkColour, AskTouchline
    tl = build("M059", backend=AgentFrameworkColour(client=BrokenClient()))
    log = [c for c in tl["colour_log"] if "proposed" in c or c.get("verdict") == "error"]
    assert log and all(c.get("verdict") in ("error", "rejected") for c in log)
    assert sum(1 for c in tl["colour_log"] if c["final"]) > 20            # templates still speak
    r = AskTouchline(client=BrokenClient()).ask("M059", T26, "What's the score?")
    assert r["verdict"] == "error_fallback" and "Eskmouth 2" in r["answer"]


@pytest.mark.parametrize("q,expect", [
    ("Who scored?", "Marsden for Eskmouth"),
    ("What's the score?", "Eskmouth 2, Dunhallow 1, after"),
    ("How many shots have Dunhallow had?", "Dunhallow 10 shots"),
    ("Who is likely to win?", "percent"),
    ("Tell me about Marsden", "Morgan Marsden of Eskmouth: 2 goals"),
])
def test_offline_router(q, expect):
    from touchline.agents import AskTouchline
    assert expect in AskTouchline(client=None).ask("M059", T26, q)["answer"]


# ------------------------------------------------------------ research desk
@pytest.mark.parametrize("q,expect,tool_name", [
    ("Who won the league?", "Ravensholt won the league on goals scored", "league_table"),
    ("Which teams were the luckiest?", "Hartsmere were the luckiest", "rank_teams"),
    ("Top scorers", "Adrien Dufour (Dunhallow) leads on goals with 29", "top_players"),
    ("Show the title race", "Ravensholt spent 31 of 38 matchweeks top", "position_history"),
    ("Ravensholt v Corriemoor", "They met 2 times", "head_to_head"),
    ("Tell me about Marsden", "Morgan Marsden (Eskmouth", "player_profile"),
    ("Best xG per 90?", "at least 900 minutes", "top_players"),
])
def test_research_offline(q, expect, tool_name):
    from touchline.research import ResearchDesk
    r = ResearchDesk(client=None).ask(q)
    assert expect in r["answer"]
    assert r["tools_used"] == [tool_name]
    assert r["cards"] and r["cards"][-1]["rows"]


def test_research_cards_are_json_and_charted():
    import json as _json
    from touchline.research import SeasonTools
    t = SeasonTools()
    for res in (t.league_table(), t.top_players("xg"), t.rank_teams("points_minus_expected"), t.position_history()):
        c = _json.loads(res)
        assert c["columns"] and c["rows"] and c["chart"]["type"] in ("bar", "diverging", "line")


def test_research_agent_verified_with_tools():
    from touchline.research import ResearchDesk
    client = ScriptedClient([("call", "rank_teams", {"stat": "points_minus_expected"}),
                             ("text", "Hartsmere were the luckiest, 8.9 points above expected; Queensreach 41 points short."),
                             ("text", "Hartsmere were the luckiest, 8.9 points above expected; Queensreach were 21.2 short.")])
    r = ResearchDesk(client=client).ask("Who's been lucky?")
    assert r["verdict"] == "accepted" and r["attempts"] == 2
    assert "41" not in r["answer"] and r["cards"][-1]["tool"] == "rank_teams"


def test_model_configuration(monkeypatch):
    from touchline import agents, llm
    assert agents.chat_client() is None and agents.model_name() is None and not llm.available()
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
    assert agents.chat_client() is not None and llm.available()
    assert agents.model_name() == "azure-openai:gpt-4.1-mini"


def test_model_moments_can_be_rationed(monkeypatch):
    from touchline.agents import AgentFrameworkColour
    monkeypatch.setenv("TOUCHLINE_LLM_MOMENTS", "prematch,half_time,full_time,substitution")
    client = ScriptedClient([("text", "SKIP")] * 400)
    tl = build("M059", backend=AgentFrameworkColour(client=client))
    called = {c["trigger"] for c in tl["colour_log"] if "proposed" in c}
    assert called <= {"prematch", "half_time", "full_time", "substitution"}
    assert len(client.prompts) <= 20                      # instead of ~150 with every moment


def test_junk_endpoint_error_is_explained():
    from touchline.agents import explain_error
    assert "AZURE_OPENAI_ENDPOINT" in explain_error(AttributeError("'str' object has no attribute 'choices'"))
