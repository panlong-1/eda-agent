import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer

import pytest

from eda_agent.agent import human_gate
from eda_agent.agent.levels import assess
from eda_agent.agent.receipt import write_receipt
from eda_agent.review_ui import ReviewApp, make_handler


def receipt(run_id: str, reviews=(), checks=None, **over) -> dict:
    r = {"run_id": run_id, "task": "t", "project": "p", "provider": "x", "model": "m", "prompt": "",
         "stop_reason": "end", "status": "done", "turns": 1, "tool_calls": 1, "tool_errors": 0,
         "usage": {}, "wall_s": 0, "trace": "",
         "gate": {"status": "passed", "rewrites": 0, "decisions": []},
         "checks_run": checks if checks is not None else [
             {"tool": "regress", "args": {"cases": ["a"]}, "status": "pass", "evidence_level": "E2",
              "summary": "1 passed, 0 failed, 0 skipped", "claim_scope": "s", "tool_versions": {"v": "1"},
              "artifacts": []}],
         "not_run": {"lint_run": True, "described_but_not_run": [], "cases_not_run": []},
         "final_claims": "", "residual_risk": {"report": "", "from_tools": []}, "reviews": list(reviews)}
    r.update(over)
    return human_gate.request_approval(r, "final report", "gate passed")


def review(verdict, *findings) -> dict:
    return {"reviewer": "rules", "context": "c", "provider": "", "model": "", "note": "", "checklist": [],
            "findings": [{"severity": s, "text": t} for s, t in findings], "verdict": verdict, "format_ok": True,
            "text": "", "tool_calls": [], "usage": {}, "wall_s": 0}


@pytest.fixture
def runs(tmp_path):
    write_receipt(receipt("warn_only"), tmp_path)
    write_receipt(receipt("with_errors", [review("accept", ("medium", "blocking assignment at rtl/a.sv:3"))]),
                  tmp_path)
    write_receipt(receipt("with_fatal", [review("reject", ("high", "multi_driven at rtl/b.sv:9"))]), tmp_path)
    return tmp_path


@pytest.fixture
def server(runs):
    app = ReviewApp(runs, token="tok123", by="Tester")
    srv = HTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(url: str, host: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def post(url: str, fields: list[tuple[str, str]]) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode()
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, data=data)) as res:
            return res.status, res.headers.get("Location", "")
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Location", "")


def saved(runs, run_id) -> dict:
    return json.loads((runs / f"{run_id}.receipt.json").read_text())


def ids(runs, run_id, level) -> list[str]:
    return [i.id for i in assess(saved(runs, run_id)) if i.level == level]


def test_token_and_host_are_required(server):
    app, base = server
    assert get(f"{base}/")[0] == 403
    assert get(f"{base}/?t=wrong")[0] == 403
    assert get(f"{base}/?t=tok123", host="evil.example:80")[0] == 403
    status, page = get(f"{base}/?t=tok123")
    assert status == 200 and "with_fatal" in page and "warn_only" in page
    assert get(f"{base}/run/..%2Fetc?t=tok123")[0] == 404
    assert post(f"{base}/run/warn_only/decide", [("action", "approve")])[0] == 403
    assert post(f"{base}/run/warn_only/decide", [("token", "no"), ("action", "approve")])[0] == 403


def test_run_page_shows_the_panel(server):
    app, base = server
    status, page = get(f"{base}/run/with_fatal?t=tok123")
    assert status == 200 and "Approve</button>" in page and "Reject</button>" in page and "Pending</button>" in page
    assert "I override the Fatal item(s)" in page and 'value="tok123"' in page


def test_warnings_only_approves_with_one_click(server, runs):
    app, base = server
    status, location = post(f"{base}/run/warn_only/decide", [("token", "tok123"), ("action", "approve")])
    assert status == 303 and "ok=1" in location
    r = saved(runs, "warn_only")
    assert r["status"] == "approved" and r["approval"]["by"] == "Tester (via review-ui)"
    assert r["approval"]["receipt_sha256"] == human_gate.content_sha256(r)
    assert "Already decided: approved" in (runs / "warn_only.receipt.html").read_text()
    status, location = post(f"{base}/run/warn_only/decide", [("token", "tok123"), ("action", "reject"),
                                                             ("reason", "changed my mind")])
    assert "ok=0" in location and saved(runs, "warn_only")["status"] == "approved"  # decisions are final


def test_errors_must_be_ticked_or_explained(server, runs):
    app, base = server
    _, location = post(f"{base}/run/with_errors/decide", [("token", "tok123"), ("action", "approve")])
    assert "ok=0" in location and saved(runs, "with_errors")["status"] == "awaiting_approval"
    ticks = [("accept", i) for i in ids(runs, "with_errors", "error")]
    _, location = post(f"{base}/run/with_errors/decide", [("token", "tok123"), ("action", "approve"), *ticks])
    assert "ok=1" in location
    a = saved(runs, "with_errors")["approval"]
    assert a["status"] == "approved" and a["reason"].startswith("accepted: ") and "rtl/a.sv:3" in a["reason"]
    assert a["levels"]["error"] == 1


def test_fatal_needs_override_and_typed_reason(server, runs):
    app, base = server
    errs = [("accept", i) for i in ids(runs, "with_fatal", "error")]
    for fields in ([], [("override", "yes")], [("reason", "fine")]):
        _, location = post(f"{base}/run/with_fatal/decide",
                           [("token", "tok123"), ("action", "approve"), *errs, *fields])
        assert "ok=0" in location
    assert saved(runs, "with_fatal")["status"] == "awaiting_approval"
    _, location = post(f"{base}/run/with_fatal/decide", [("token", "tok123"), ("action", "approve"), *errs,
                                                         ("override", "yes"), ("reason", "UART is out of scope")])
    assert "ok=1" in location
    a = saved(runs, "with_fatal")["approval"]
    assert a["override"] and "UART is out of scope" in a["reason"] and a["levels"]["fatal"] == 1


def test_pending_keeps_the_run_open_and_reject_needs_a_reason(server, runs):
    app, base = server
    _, location = post(f"{base}/run/with_fatal/decide", [("token", "tok123"), ("action", "pending"),
                                                         ("note", "need the full regression")])
    assert "ok=1" in location
    r = saved(runs, "with_fatal")
    assert r["status"] == "awaiting_approval" and r["approval"]["notes"][0]["note"] == "need the full regression"
    _, location = post(f"{base}/run/with_fatal/decide", [("token", "tok123"), ("action", "reject")])
    assert "ok=0" in location
    fatal = ids(runs, "with_fatal", "fatal")
    _, location = post(f"{base}/run/with_fatal/decide", [("token", "tok123"), ("action", "reject"),
                                                         ("because", fatal[0])])
    r = saved(runs, "with_fatal")
    assert r["status"] == "rejected" and r["approval"]["reason"].startswith("because: ")


def test_index_lists_open_runs_first(runs):
    app = ReviewApp(runs, token="t", by="T")
    app.decide("warn_only", {"action": ["approve"]})
    page = app.index()
    assert page.index("with_errors") < page.index("warn_only") and page.index("with_fatal") < page.index("warn_only")
