"""A card's life across several work sessions: each card keeps its own cost, and it closes only when the card itself is finished.

The scenario: on day one you work on three cards (an analysis and two specs) while five more stay open untouched; on day two you
pick two of them up again and carry one to the end. Everything here is the real code (requests, work sessions, the cost ledger and
the report); only Claude Code is replaced by a launcher that records the workers and a ledger entry for what each one "cost"."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import report  # noqa: E402
import usage  # noqa: E402
from features import Features  # noqa: E402
from test_auto_review import Fake  # noqa: E402
from worksession import WorkSessions  # noqa: E402

SON, OPU = "claude-sonnet-5-5", "claude-opus-5-5"


class Clock:
    t = datetime(2026, 11, 8, 9, 0, 0)

    def __call__(self):
        return self.t


class World:
    def __init__(self, tmp: Path):
        self.root = tmp / "state"
        self.clock = Clock()
        self.ws = WorkSessions(tmp / "ws.json", now=self.clock)
        self.ws.ensure()
        self.fake, self.workers, self.ledger = Fake(), [], {}

        def launch(project, task, model, extra_dirs, label=None):
            w = self.fake.launch(project, task, model, extra_dirs, label)
            self.workers.append({"id": w["id"], "project": project, "session": self.ws.current_id(), "request": "", "phase": ""})
            return w

        def tag(worker_id, request, phase):                      # what chief.tag_worker does on the real launcher
            next(x for x in self.workers if x["id"] == worker_id).update(request=request, phase=phase)

        self.fs = Features(self.root, launch, self.fake.is_active, session=self.ws.current_id, on_launch=tag)

    # ---- what a person does
    def new_card(self, title, kind="feature", project="Alfa"):
        return self.fs.create(project, title, "desc", None, kind=kind)["id"]

    def spend(self, cost, model=SON):
        """The worker that was just launched did its work and cost this much (what usage.py would read from its log)."""
        w = self.workers[-1]
        row = {"messages": 1, "input": 10, "output": 5, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 100, "cost_usd": cost, "unpriced_messages": 0}
        self.ledger[w["id"]] = {"project": w["project"], "session": w["session"], "request": w["request"], "phase": w["phase"], "models": {model: row}}

    def finish(self, card, artifact):
        """The card's current worker finishes and leaves its artifact: the card now waits for a decision."""
        self.fake.active.discard(self.cards()[card]["worker_id"])
        folder = self.root / "features" / card
        folder.mkdir(parents=True, exist_ok=True)
        (folder / artifact).write_text("# done", encoding="utf-8")
        return self.cards()[card]

    def approve(self, card):
        return self.fs.approve(card)

    def end_session(self):
        out = self.ws.end(report.totals_now(self.ledger, self.ws.current_id()))
        return out["ended"]["id"]

    def next_day(self):
        self.clock.t += timedelta(days=1)

    # ---- what the dashboard would show
    def cards(self):
        return {f["id"]: f for f in self.fs.list()}

    def cost(self, card):
        return usage.request_costs(self.ledger).get(card)

    def view(self, session_id):
        rep = report.build(self.ws, self.ledger, self.fs.list(), self.workers)
        return next(s for s in [rep["current"], *rep["sessions"]] if s and s["id"] == session_id)

    def on_dashboard(self, session_id):
        return {f["id"] for f in self.fs.list() if report.in_scope(f, session_id)}


@pytest.fixture
def week(tmp_path):
    """Day one: three cards worked on, five more open. Returns the world and the ids."""
    w = World(tmp_path)
    ids = {}
    ids["analysis"] = w.new_card("Why is the sync slow?", kind="analysis")
    w.spend(0.40)
    ids["spec_b"] = w.new_card("Two-factor login")
    w.spend(0.60)
    ids["spec_c"] = w.new_card("Webhook retries")
    w.spend(0.50)
    ids["backlog"] = [w.new_card(f"Backlog {i}") for i in range(5)]                    # open, but no AI spend yet
    w.finish(ids["analysis"], "report.md")
    w.finish(ids["spec_b"], "spec.md")
    w.finish(ids["spec_c"], "spec.md")
    ids["s1"] = w.end_session()
    return w, ids


def day_two(w, ids):
    """Day two: B goes all the way to the end, C advances to the implementation, A is left alone."""
    w.next_day()
    w.ws.ensure()
    b, c = ids["spec_b"], ids["spec_c"]
    w.approve(b)
    w.spend(1.0)                                              # plan
    w.finish(b, "plan.md")
    w.approve(b)
    w.spend(4.0, OPU)                                         # implementation
    w.finish(b, "report.md")
    w.approve(b)
    w.spend(0.75)                                             # review
    w.finish(b, "review.md")
    final = w.approve(b)
    w.approve(c)
    w.spend(0.9)                                              # C's plan
    w.finish(c, "plan.md")
    w.approve(c)
    w.spend(3.5, OPU)                                         # C's implementation, still running
    ids["s2"] = w.ws.current_id()
    return final


def test_every_card_has_its_own_cost_after_the_first_day(week):
    w, ids = week
    assert w.cost(ids["analysis"])["cost_usd"] == pytest.approx(0.40)
    assert w.cost(ids["spec_b"])["cost_usd"] == pytest.approx(0.60) and w.cost(ids["spec_c"])["cost_usd"] == pytest.approx(0.50)
    assert all(w.cost(i) is None for i in ids["backlog"])                                       # no spend, no pill


def test_no_card_is_closed_by_a_session_ending(week):
    w, ids = week
    assert {f["status"] for f in w.fs.list()} == {"awaiting", "working"}                        # nothing is done
    assert all("closed" not in f for f in w.fs.list())
    everything = {ids["analysis"], ids["spec_b"], ids["spec_c"], *ids["backlog"]}
    assert w.on_dashboard(ids["s1"]) == everything and w.on_dashboard("the-next-session") == everything   # all eight carry over


def test_the_first_session_lists_the_cards_it_worked_on_even_though_none_was_closed(week):
    w, ids = week
    cards = {c["id"]: c for c in w.view(ids["s1"])["cards"]}
    assert set(cards) == {ids["analysis"], ids["spec_b"], ids["spec_c"]}
    assert all(c["cost_session"] == pytest.approx(c["cost_total"]) and not c["closed_here"] for c in cards.values())
    assert w.view(ids["s1"])["requests"] == []                                                  # closed: none
    assert w.view(ids["s1"])["cost_usd"] == pytest.approx(1.5)


def test_a_card_keeps_adding_up_across_sessions(week):
    w, ids = week
    day_two(w, ids)
    b, c = w.cost(ids["spec_b"]), w.cost(ids["spec_c"])
    assert b["cost_usd"] == pytest.approx(0.6 + 1.0 + 4.0 + 0.75) and b["sessions"] == 2 and b["workers"] == 4
    assert c["cost_usd"] == pytest.approx(0.5 + 0.9 + 3.5) and c["sessions"] == 2 and c["workers"] == 3
    assert set(b["by_phase"]) == {"spec", "plan", "build", "review"} and b["by_phase"]["build"]["cost_usd"] == pytest.approx(4.0)
    assert w.cost(ids["analysis"])["cost_usd"] == pytest.approx(0.40) and w.cost(ids["analysis"])["sessions"] == 1   # untouched on day two
    assert all(w.cost(i) is None for i in ids["backlog"])


def test_a_card_closes_only_when_it_is_finished_and_in_the_session_that_finished_it(week):
    w, ids = week
    final = day_two(w, ids)
    cards = w.cards()
    assert final["status"] == "done" and final["closed"]["session"] == ids["s2"]
    assert cards[ids["spec_b"]]["status"] == "done"
    assert cards[ids["spec_c"]]["status"] != "done" and cards[ids["analysis"]]["status"] != "done"        # still open, still carrying over
    assert all(cards[i]["status"] != "done" for i in ids["backlog"])
    assert ids["spec_b"] in w.on_dashboard(ids["s2"])                                           # shown as done, in the session that closed it
    assert ids["spec_b"] not in w.on_dashboard(ids["s1"]) and ids["spec_b"] not in w.on_dashboard("the-next-session")
    assert {ids["spec_c"], ids["analysis"], *ids["backlog"]} <= w.on_dashboard("the-next-session")


def test_the_second_session_shows_what_each_card_cost_that_day_and_so_far(week):
    w, ids = week
    day_two(w, ids)
    cards = {c["id"]: c for c in w.view(ids["s2"])["cards"]}
    assert set(cards) == {ids["spec_b"], ids["spec_c"]}                                         # the analysis and the backlog were not touched
    b, c = cards[ids["spec_b"]], cards[ids["spec_c"]]
    assert b["cost_session"] == pytest.approx(5.75) and b["cost_total"] == pytest.approx(6.35) and b["closed_here"] is True
    assert c["cost_session"] == pytest.approx(4.4) and c["cost_total"] == pytest.approx(4.9) and c["closed_here"] is False
    assert [r["id"] for r in w.view(ids["s2"])["requests"]] == [ids["spec_b"]]                  # the only one closed in session two


def test_what_a_past_session_cost_is_frozen_while_its_cards_keep_growing(week):
    w, ids = week
    day_two(w, ids)
    first = w.view(ids["s1"])
    assert first["cost_usd"] == pytest.approx(1.5)                                              # day two did not change day one
    b = next(c for c in first["cards"] if c["id"] == ids["spec_b"])
    assert b["cost_session"] == pytest.approx(0.6) and b["cost_total"] == pytest.approx(6.35)    # ...but the card's total includes day two


def test_the_cards_add_up_to_everything_that_was_spent(week):
    w, ids = week
    day_two(w, ids)
    total = usage.aggregate(w.ledger)["cost_usd"]
    assert sum(c["cost_usd"] for c in usage.request_costs(w.ledger).values()) == pytest.approx(total) == pytest.approx(0.4 + 6.35 + 4.9)
    assert w.view(ids["s1"])["cost_usd"] + w.view(ids["s2"])["cost_usd"] == pytest.approx(total)   # and so do the two sessions


def test_a_closed_card_keeps_its_cost_when_later_sessions_no_longer_show_it(week):
    w, ids = week
    day_two(w, ids)
    w.end_session()
    w.next_day()
    w.ws.ensure()
    third = w.ws.current_id()
    assert ids["spec_b"] not in w.on_dashboard(third)
    assert w.cost(ids["spec_b"])["cost_usd"] == pytest.approx(6.35)                            # gone from the cards, not from the books
    rows = {r["id"]: r for r in report.build(w.ws, w.ledger, w.fs.list(), w.workers)["requests"]}
    assert rows[ids["spec_b"]]["status"] == "done" and rows[ids["spec_b"]]["cost_usd"] == pytest.approx(6.35)


def test_cancelling_a_card_in_a_later_session_closes_it_there_with_the_cost_so_far(week):
    w, ids = week
    w.next_day()
    w.ws.ensure()
    out = w.fs.cancel(ids["spec_c"])
    assert out["status"] == "cancelled" and out["closed"]["session"] == w.ws.current_id()
    assert w.cost(ids["spec_c"])["cost_usd"] == pytest.approx(0.5)
    assert ids["spec_c"] in w.on_dashboard(w.ws.current_id()) and ids["spec_c"] not in w.on_dashboard(ids["s1"])
