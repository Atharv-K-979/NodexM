import pytest
from langchain_core.messages import AIMessage

from nodexm import llm as llm_mod
from nodexm.llm import LLMClient, LLMError


class FakeChat:
    def __init__(self, name, behaviour, calls):
        self.name, self.behaviour, self.calls = name, behaviour, calls

    def invoke(self, messages):
        self.calls.append(self.name)
        outcome = self.behaviour[self.name]
        if isinstance(outcome, Exception):
            raise outcome
        return AIMessage(content=outcome, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(LLMClient, "_chain", staticmethod(lambda tier: ["missing", "busy", "good"]))
    llm_mod._MISSING.clear()
    calls: list[str] = []
    behaviour = {
        "missing": RuntimeError("Error calling model 'missing' (NOT_FOUND): 404 NOT_FOUND"),
        "busy": RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand."),
        "good": "hello",
    }
    c = LLMClient("key")
    monkeypatch.setattr(c, "_model", lambda name, temperature: FakeChat(name, behaviour, calls))
    yield c, calls, behaviour
    llm_mod._MISSING.clear()


def test_failed_models_are_skipped_after_the_first_failure(client):
    c, calls, _ = client
    assert c.text("lite", "s", "u") == "hello"
    assert calls == ["missing", "busy", "good"]
    calls.clear()
    assert c.text("lite", "s", "u") == "hello"
    assert calls == ["good"]  # no second round-trip to the missing or overloaded model
    assert c.usage.calls == 2


def test_missing_models_stay_skipped_for_new_clients_but_busy_ones_do_not(client, monkeypatch):
    c, calls, behaviour = client
    c.text("lite", "s", "u")
    fresh = LLMClient("key")
    monkeypatch.setattr(fresh, "_model", lambda name, temperature: FakeChat(name, behaviour, calls))
    calls.clear()
    fresh.text("lite", "s", "u")
    assert calls == ["busy", "good"]


def test_all_models_failing_raises(client):
    c, _, behaviour = client
    behaviour["good"] = RuntimeError("504 DEADLINE_EXCEEDED")
    with pytest.raises(LLMError):
        c.text("lite", "s", "u")
    behaviour["good"] = "back"
    assert c.text("lite", "s", "u") == "back"  # when everything was skipped, it tries again rather than giving up
