import asyncio
from types import SimpleNamespace as NS

import pytest

from app.agent import MAX_FACTS, Agent, clean_history, truncate
from app.bot import chunk
from app.config import Settings
from app.memory import InMemoryStore


class FakeTools:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return [
            {
                "name": "calendar_list_events",
                "description": "x",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    async def call(self, name, args):
        self.calls.append((name, args))
        return "[]"


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    """Returns scripted responses and snapshots what the model was sent."""

    def __init__(self, script):
        self.script, self.seen = list(script), []
        self.messages = self

    async def create(self, **kw):
        self.seen.append({**kw, "messages": list(kw["messages"])})
        return self.script.pop(0)


def make(script, store=None):
    store = store or InMemoryStore()
    tools = FakeTools()
    client = FakeClient(script)
    settings = Settings(
        anthropic_api_key="k", model="m", timezone="Asia/Kolkata", allowed_user_ids={1}
    )
    return Agent(settings, store, tools, client), store, tools, client


def run(coro):
    return asyncio.run(coro)


def end(t):
    return NS(stop_reason="end_turn", content=[text(t)])


def uses(*blocks):
    return NS(stop_reason="tool_use", content=list(blocks))


def test_plain_reply_and_history_saved():
    agent, store, _, _ = make([end("hi")])
    assert run(agent.reply(1, "hello")) == "hi"
    assert store.recent_messages(1, 10) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_mcp_tool_roundtrip():
    agent, _, tools, client = make(
        [uses(tool_use("t1", "calendar_list_events", {"time_min": "a"})), end("You are free.")]
    )
    assert run(agent.reply(1, "am I free?")) == "You are free."
    assert tools.calls == [("calendar_list_events", {"time_min": "a"})]
    assert client.seen[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


def test_remember_persists_and_is_injected_next_turn():
    agent, store, _, client = make(
        [
            uses(tool_use("t1", "remember", {"key": "wake", "value": "6am"})),
            end("Noted."),
            end("ok"),
        ]
    )
    run(agent.reply(1, "I wake at 6am"))
    assert store.facts(1) == {"wake": "6am"}
    run(agent.reply(1, "anything?"))
    assert "wake: 6am" in client.seen[2]["system"]


def test_forget_removes_fact():
    store = InMemoryStore()
    store.remember(1, "a", "1")
    agent, _, _, _ = make([uses(tool_use("t1", "forget", {"key": "a"})), end("Done.")], store=store)
    run(agent.reply(1, "forget a"))
    assert store.facts(1) == {}


def test_remember_rejects_empty_and_enforces_cap():
    store = InMemoryStore()
    agent, *_ = make([end("x")], store=store)
    assert run(agent._remember(1, {"key": " ", "value": "v"})).startswith("ERROR")
    for i in range(MAX_FACTS):
        store.remember(1, f"k{i}", "v")
    assert "memory full" in run(agent._remember(1, {"key": "new", "value": "v"}))
    assert run(agent._remember(1, {"key": "k0", "value": "updated"})) == "saved"


def test_remember_truncates_long_values():
    store = InMemoryStore()
    agent, *_ = make([end("x")], store=store)
    run(agent._remember(1, {"key": "k", "value": "v" * 5000}))
    assert len(store.facts(1)["k"]) == 500


def test_tool_error_goes_back_to_model_not_crash():
    class Boom(FakeTools):
        async def call(self, name, args):
            raise RuntimeError("api down")

    agent, _, _, client = make(
        [uses(tool_use("t1", "calendar_list_events", {})), end("Calendar is down.")]
    )
    agent.tools = Boom()
    assert run(agent.reply(1, "x")) == "Calendar is down."
    assert "ERROR: RuntimeError" in client.seen[1]["messages"][-1]["content"][0]["content"]


def test_loop_cap():
    loop = uses(tool_use("t", "calendar_list_events", {}))
    agent, *_ = make([loop] * 8)
    assert "stuck" in run(agent.reply(1, "x"))


def test_history_window_never_starts_with_assistant():
    store = InMemoryStore()
    for i in range(30):
        store.add_exchange(1, f"u{i}", f"a{i}")
    agent, _, _, client = make([end("ok")], store=store)
    agent.settings.history_turns = 5  # odd window cuts mid-exchange
    run(agent.reply(1, "now"))
    assert client.seen[0]["messages"][0]["role"] == "user"


def test_clean_history():
    msgs = [
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a2"},
    ]
    assert clean_history(msgs) == msgs[1:]
    assert clean_history([{"role": "assistant", "content": "a"}]) == []


def test_truncate():
    assert truncate("abc", 10) == "abc"
    out = truncate("x" * 100, 10)
    assert out.startswith("x" * 10) and "truncated 90" in out


def test_facts_are_per_chat():
    s = InMemoryStore()
    s.remember(1, "a", "1")
    assert s.facts(2) == {}
    assert s.forget(1, "a") and not s.forget(1, "a")


def test_chunking():
    assert chunk("") == [""]
    assert chunk("a" * 4001) == ["a" * 4000, "a"]


def test_settings_validation(monkeypatch):
    s = Settings(anthropic_api_key="", telegram_token="t", allowed_user_ids={1})
    with pytest.raises(SystemExit):
        s.validate()
    s = Settings(anthropic_api_key="k", telegram_token="t", allowed_user_ids=set())
    with pytest.raises(SystemExit):
        s.validate()
    Settings(anthropic_api_key="k", telegram_token="t", allowed_user_ids={1}).validate()
