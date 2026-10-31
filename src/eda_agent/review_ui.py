"""A local page to decide runs by clicking: `eda-agent review-ui [--port 8799] [--runs-dir runs]`.

    index     every receipt in runs/: status, Fatal / Error / Warning counts; open runs first
    run page  the receipt, with Approve / Pending / Reject under the decision card

A click calls the same human_gate functions as the command line, with the same rules: Warnings
only, one click; Error items, tick each one you accept (or write a reason); Fatal items, tick
the override and type a reason, the friction is on purpose. Reject takes ticked items or text.
The decision records the approver (git user.name, "via review-ui"), the reason and the
receipt's sha256, then re-renders the receipt files.

Safety: the server binds 127.0.0.1 only, answers only Host: localhost / 127.0.0.1, and needs
a random token made at each start (in the printed URL, and in every form). It reads only
runs/<id>.receipt.json (and the run's trace, for the tables) and writes only that receipt and
its .md/.html. Standard library only (http.server).
"""
from __future__ import annotations

import hmac
import html
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .agent import human_gate
from .agent.levels import LEVELS, TITLE, assess, counts
from .agent.receipt import write_receipt
from .agent.receipt_tables import with_trace_data
from .agent.receipt_view import CSS, RUN_CLASS, _badge, _level_badge, render_html

RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
MAX_BODY = 64 * 1024
OPEN = (human_gate.AWAITING, "pending")


def e(text) -> str:
    return html.escape(str(text), quote=True)


class ReviewApp:
    """The pages and the decisions, without the HTTP part (so tests can call it directly)."""

    def __init__(self, runs_dir: Path, token: str | None = None, by: str | None = None):
        self.runs_dir = runs_dir
        self.token = token or secrets.token_urlsafe(18)
        self.by = by or human_gate.default_approver()

    def check_token(self, given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(given, self.token)

    def _load(self, run_id: str) -> dict:
        if not RUN_ID.match(run_id or ""):
            raise human_gate.ApprovalError("no such run")
        receipt, _ = human_gate.load_receipt(run_id, self.runs_dir)
        return receipt

    def link(self, path: str, **query) -> str:
        q = "&".join(f"{k}={quote(str(v))}" for k, v in {"t": self.token, **query}.items())
        return f"{path}?{q}"

    # ---------------------------------------------------------------- pages

    def index(self) -> str:
        rows = []
        for path in self.runs_dir.glob("*.receipt.json"):
            try:
                r = json.loads(path.read_text(encoding="utf-8"))
                c = counts(assess(with_trace_data(r, self.runs_dir)))
            except (ValueError, KeyError, OSError):
                continue
            rows.append((r["status"] not in OPEN, -path.stat().st_mtime, r, c))
        rows.sort(key=lambda x: (x[0], x[1]))
        body = []
        for _, _, r, c in rows:
            a = r.get("approval") or {}
            who = f"{a.get('by')} at {a.get('at')}" if a.get("status") in ("approved", "rejected") else (
                f"{len(a.get('notes', []))} note(s)" if a.get("notes") else "")
            body.append(
                f'<tr><td><a href="{e(self.link("/run/" + r["run_id"]))}"><b>{e(r["run_id"])}</b></a></td>'
                f'<td>{_badge(r["status"], RUN_CLASS.get(r["status"], "muted"))}</td>'
                + "".join(f'<td class="num">{_badge(str(c[lvl]), lvl if c[lvl] else "muted")}</td>' for lvl in LEVELS)
                + f'<td>{e(r["task"][:110])}{"…" if len(r["task"]) > 110 else ""}</td>'
                + f'<td class="small muted">{e(who)}</td></tr>')
        table = ('<div class="tbl"><table class="wide"><tr><th>run</th><th>status</th>'
                 + "".join(f'<th class="num">{TITLE[lvl]}</th>' for lvl in LEVELS)
                 + "<th>task</th><th>decided / notes</th></tr>" + "".join(body) + "</table></div>") if body \
            else f'<p class="muted">No receipts in {e(self.runs_dir)}.</p>'
        return self._page("Runs to review", f'<h1>Runs to review</h1><p class="note">{e(self.runs_dir.resolve())} · '
                                            f"deciding as {e(self.by)} (via review-ui)</p>{table}")

    def run_page(self, run_id: str, message: str = "", ok: bool = True) -> str:
        receipt = self._load(run_id)
        view = with_trace_data(receipt, self.runs_dir)
        page = render_html(view, panel=self._panel(view, message, ok))
        back = f'<p><a href="{e(self.link("/"))}">← all runs</a></p>'
        return page.replace("<main>", "<main>" + back, 1)

    def _page(self, title: str, body: str) -> str:
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1"><title>{e(title)}</title><style>{CSS}</style>'
                f"</head><body><main>{body}</main></body></html>")

    def _panel(self, r: dict, message: str, ok: bool) -> str:
        msg = f'<div class="msg {"ok" if ok else "bad"}">{e(message)}</div>' if message else ""
        a = r.get("approval") or {}
        if r["status"] not in OPEN or a.get("status") in ("approved", "rejected"):
            return f'<div class="panel"><b>Decision</b>{msg}<p class="muted">This run is {e(r["status"])}; ' \
                   "decisions are final.</p></div>"
        issues = assess(r)
        c = counts(issues)
        fatal = [i for i in issues if i.level == "fatal"]
        errors = [i for i in issues if i.level == "error"]
        action = e(f"/run/{r['run_id']}/decide")
        hidden = f'<input type="hidden" name="token" value="{e(self.token)}">'

        def ticks(name: str, items, prefix: str) -> str:
            return "".join(f'<label><input type="checkbox" name="{name}" value="{i.id}"> {e(prefix)}'
                           f"{_level_badge(i.level)} {e(i.label()[:300])}</label>" for i in items)

        if fatal:
            approve = (f"<p><b>Approve</b> is blocked by {len(fatal)} Fatal item(s). To approve anyway, tick the "
                       "override and type a reason; both are recorded.</p>"
                       + "".join(f"<p>{_level_badge(i.level)} {e(i.label()[:300])}</p>" for i in fatal)
                       + '<label><input type="checkbox" name="override" value="yes"> I override the Fatal item(s) '
                         "above</label>" + ticks("accept", errors, "I accept: ")
                       + '<textarea name="reason" placeholder="reason (required)"></textarea>')
        elif errors:
            approve = ("<p><b>Approve</b>: tick each Error item you accept, or write a reason.</p>"
                       + ticks("accept", errors, "I accept: ")
                       + '<textarea name="reason" placeholder="reason (optional if every item is ticked)"></textarea>')
        else:
            approve = "<p><b>Approve</b>: Warnings only, one click.</p>"
        reject = ("<p><b>Reject</b>: tick what is wrong, or write a reason.</p>" + ticks("because", fatal + errors, "")
                  + '<textarea name="reason" placeholder="reason (optional if something is ticked)"></textarea>')
        return (f'<div class="panel"><b>Decision</b> · deciding as {e(self.by)} (via review-ui) · '
                f"{e(' · '.join(f'{TITLE[k]} {v}' for k, v in c.items()))}{msg}"
                f'<form method="post" action="{action}">{hidden}<input type="hidden" name="action" value="approve">'
                f'{approve}<button class="b-approve" type="submit">Approve</button></form>'
                f'<form method="post" action="{action}">{hidden}<input type="hidden" name="action" value="pending">'
                '<p><b>Pending</b>: decide later; a note is recorded, the run stays open.</p>'
                '<textarea name="note" placeholder="what is missing (optional)"></textarea>'
                '<button class="b-pending" type="submit">Pending</button></form>'
                f'<form method="post" action="{action}">{hidden}<input type="hidden" name="action" value="reject">'
                f'{reject}<button class="b-reject" type="submit">Reject</button></form></div>')

    # ---------------------------------------------------------------- decisions

    def decide(self, run_id: str, form: dict[str, list[str]]) -> str:
        """Apply one form post; returns the message to show. ApprovalError when refused."""
        receipt = self._load(run_id)
        view = with_trace_data(receipt, self.runs_dir)
        issues = {i.id: i for i in assess(view)}
        action = (form.get("action") or [""])[0]
        text = (form.get("reason") or [""])[0].strip()
        note = (form.get("note") or [""])[0].strip()
        if action == "pending":
            human_gate.defer(receipt, by=self.by, note=note, via="review-ui")
            write_receipt(receipt, self.runs_dir)
            return "Recorded as pending: the run stays open."
        if action == "approve":
            fatal = [i for i in issues.values() if i.level == "fatal"]
            errors = [i for i in issues.values() if i.level == "error"]
            accepted = [issues[x] for x in form.get("accept", []) if x in issues and issues[x].level == "error"]
            override = (form.get("override") or [""])[0] == "yes"
            if fatal and not (override and text):
                raise human_gate.ApprovalError("Fatal items: tick the override and type a reason to approve")
            if errors and not text and len(accepted) < len(errors):
                raise human_gate.ApprovalError("Error items: tick every one you accept, or write a reason")
            parts = ((["accepted: " + "; ".join(i.label() for i in accepted)] if accepted else [])
                     + ([text] if text else []))
            human_gate.decide(receipt, "approve", by=self.by, reason=" | ".join(parts), override=override,
                              via="review-ui")
        elif action == "reject":
            because = [issues[x] for x in form.get("because", []) if x in issues]
            parts = ((["because: " + "; ".join(i.label() for i in because)] if because else [])
                     + ([text] if text else []))
            if not parts:
                raise human_gate.ApprovalError("Reject: tick what is wrong, or write a reason")
            human_gate.decide(receipt, "reject", by=self.by, reason=" | ".join(parts), via="review-ui")
        else:
            raise human_gate.ApprovalError("unknown action")
        write_receipt(receipt, self.runs_dir)
        return f"Recorded: {receipt['status']} by {receipt['approval']['by']}."


def make_handler(app: ReviewApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "eda-agent-review-ui"

        def log_message(self, fmt, *args):  # keep the token out of the terminal log
            print(f"{self.command} {urlparse(self.path).path} -> {args[1] if len(args) > 1 else ''}", flush=True)

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _send(self, code: int, body: str, location: str | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                                                        "form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if not self._host_ok() or not app.check_token((q.get("t") or [None])[0]):
                return self._send(403, "forbidden: open the URL printed by eda-agent review-ui")
            if url.path == "/":
                return self._send(200, app.index())
            m = re.fullmatch(r"/run/([^/]+)", url.path)
            if not m:
                return self._send(404, "not found")
            try:
                msg = (q.get("msg") or [""])[0]
                return self._send(200, app.run_page(m[1], msg, ok=(q.get("ok") or ["1"])[0] == "1"))
            except human_gate.ApprovalError:
                return self._send(404, "no such run")

        def do_POST(self):
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if not self._host_ok() or length > MAX_BODY:
                return self._send(403, "forbidden")
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
            if not app.check_token((form.get("token") or [None])[0]):
                return self._send(403, "forbidden: bad or missing token")
            m = re.fullmatch(r"/run/([^/]+)/decide", url.path)
            if not m:
                return self._send(404, "not found")
            try:
                message, ok = app.decide(m[1], form), "1"
            except human_gate.ApprovalError as err:
                if str(err) == "no such run" or str(err).startswith("no receipt"):
                    return self._send(404, "no such run")
                message, ok = f"Not recorded: {err}", "0"
            return self._send(303, "", location=app.link(f"/run/{m[1]}", msg=message, ok=ok))

    return Handler


def serve(runs_dir: Path, port: int = 8799, by: str | None = None) -> int:
    app = ReviewApp(runs_dir, by=by)
    server = HTTPServer(("127.0.0.1", port), make_handler(app))
    found = len(list(runs_dir.glob("*.receipt.json")))
    print(f"eda-agent review-ui: {found} receipt(s) in {runs_dir.resolve()}", flush=True)
    print(f"open: http://localhost:{server.server_address[1]}/?t={app.token}", flush=True)
    print("(only this computer can reach it; the token changes at every start; Ctrl+C stops it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
