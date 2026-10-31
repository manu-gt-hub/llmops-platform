"""Agent tools, guardrails, grading and the run loop (fake LLM, no network)."""

import pytest

from agent import evals
from agent.llm import GatewayRejected, ProviderChoice, ToolCall, Turn
from agent.runner import LoopDetector, run_task
from agent.tools import ToolError, calculator, read_doc, search_docs


def test_calculator_evaluates_arithmetic():
    assert calculator("29 * 12 * 12 * 0.85") == "3549.6"
    assert calculator("(5 * 12) + 3 * 29") == "147"


@pytest.mark.parametrize("expression", ["__import__('os')", "open('x')", "2 ** 1000", "1 / 0", "a + 1"])
def test_calculator_rejects_unsafe_or_invalid_input(expression):
    with pytest.raises(ToolError):
        calculator(expression)


def test_search_finds_the_relevant_section():
    assert "[plans]" in search_docs("single sign-on SSO plan")


def test_read_doc_unknown_lists_available():
    with pytest.raises(ToolError, match="Available: expenses"):
        read_doc("nope")


def test_loop_detector_triggers_on_repeated_call():
    detector = LoopDetector(limit=3)
    assert not detector.check("search_docs", {"query": "a"})
    assert not detector.check("search_docs", {"query": "b"})
    assert not detector.check("search_docs", {"query": "a"})
    assert detector.check("search_docs", {"query": "a"})


@pytest.mark.parametrize("task, answer, passed", [
    ({"expected_number": 3549.6}, "It costs $3,549.60 per year.", True),
    ({"expected_number": 15}, "Within 1 hour.", False),
    ({"expected_keywords": ["manager"], "forbidden_keywords": ["director"]}, "Your line manager.", True),
    ({"expected_keywords": ["manager"], "forbidden_keywords": ["director"]}, "Manager or director.", False),
    ({"expected_number": 10}, "", False),
])
def test_grade(task, answer, passed):
    assert evals.grade(task, answer)[0] is passed


def test_gate_flags_quality_and_cost_regressions():
    baseline = {"min_pass_rate": 0.8, "avg_cost_usd": 0.01, "cost_tolerance": 0.2}
    assert evals.gate({"pass_rate": 0.9, "avg_cost_usd": 0.011}, baseline) == []
    failures = evals.gate({"pass_rate": 0.5, "avg_cost_usd": 0.02}, baseline)
    assert len(failures) == 2


class FakeReporter:
    def __init__(self):
        self.steps, self.finished = [], None

    def start(self, **run):
        self.run = run

    def step(self, run_id, **step):
        self.steps.append(step)

    def finish(self, run_id, **result):
        self.finished = result
        return {"cost_usd": 0.01, "total_tokens": 100}


class ScriptedConversation:
    """Plays back a fixed list of turns (or exceptions)."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.results = []

    def step(self, headers):
        assert headers["X-Run-Id"].startswith("run-")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def add_results(self, outcomes):
        self.results.append(outcomes)


CHOICE = ProviderChoice("anthropic", "claude-opus-5-5")


def _tool_turn(name, args):
    return Turn("", [ToolCall("id", name, args)], "tool_use", "claude-opus-5-5")


def test_run_succeeds_and_reports_every_step():
    conversation = ScriptedConversation([
        _tool_turn("calculator", {"expression": "2 + 2"}),
        Turn("The answer is 4.", [], "end_turn", "claude-opus-5-5"),
    ])
    reporter = FakeReporter()
    result = run_task("2+2?", CHOICE, reporter, conversation=conversation)
    assert result.status == "success" and result.answer == "The answer is 4."
    assert [s["kind"] for s in reporter.steps] == ["llm", "tool", "llm"]
    assert conversation.results[0][0].output == "4"
    assert reporter.finished["tool_calls"] == 1


def test_tool_errors_are_returned_to_the_model():
    conversation = ScriptedConversation([
        _tool_turn("read_doc", {"name": "missing"}),
        Turn("Not in the handbook.", [], "end_turn"),
    ])
    result = run_task("?", CHOICE, FakeReporter(), conversation=conversation)
    assert conversation.results[0][0].is_error
    assert result.tool_errors == 1 and result.status == "success"


def test_loop_is_killed():
    conversation = ScriptedConversation([_tool_turn("search_docs", {"query": "x"})] * 5)
    result = run_task("?", CHOICE, FakeReporter(), conversation=conversation)
    assert result.status == "killed" and result.stop_reason == "loop_detected" and result.loop_detected


def test_gateway_rejection_kills_the_run():
    conversation = ScriptedConversation([GatewayRejected("run_cost_limit", "limit reached")])
    result = run_task("?", CHOICE, FakeReporter(), conversation=conversation)
    assert (result.status, result.stop_reason) == ("killed", "run_cost_limit")


def test_refusal_is_a_failed_run():
    conversation = ScriptedConversation([Turn("", [], "refusal")])
    result = run_task("?", CHOICE, FakeReporter(), conversation=conversation)
    assert (result.status, result.stop_reason) == ("failed", "refusal")
