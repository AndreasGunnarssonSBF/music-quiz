"""Snabbtest av servern utan internetadress: python3 tests/test_server.py"""
import http.client
import io
import json
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["MUSIKQUIZ_NO_TUNNEL"] = "1"
os.environ["MUSIKQUIZ_HOME"] = tempfile.mkdtemp(prefix="musikquiz-test-")

from server import App  # noqa: E402

app = App()
ADMIN = app.start(range(18765, 18775))
PUBLIC = app.public_port
H = {"X-Musikquiz": "1", "Content-Type": "application/json"}


def call(port, method, path, body=None, headers=None, conn=None):
    c = conn or http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    c.request(method, path, body=data, headers=headers or {})
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


fails = 0


def check(name, ok):
    global fails
    print(("OK   " if ok else "FEL  ") + name)
    fails += 0 if ok else 1


# Bibliotek och start
s, info = call(ADMIN, "GET", "/api/admin/info")
check("exempelquiz skapas", [q["id"] for q in info["quizzes"]] == ["exempelquiz"])
ex = info["quizzes"][0]
check("exempelquiz har låtrunda och är klart att starta", ex["rounds"] == 2 and not ex["problems"])
check("CSRF-skydd", call(ADMIN, "POST", "/api/admin/start", {"id": "exempelquiz"})[0] == 403)
check("Host-skydd", call(ADMIN, "GET", "/api/admin/info", headers={"Host": "evil.test"})[0] == 403)
check("start", call(ADMIN, "POST", "/api/admin/start", {"id": "exempelquiz"}, H)[0] == 200)

# Lagen via publika porten, flera anrop på samma keep-alive-anslutning
c = http.client.HTTPConnection("127.0.0.1", PUBLIC, timeout=10)
s, j = call(PUBLIC, "POST", "/api/join", {"name": "Testlaget"}, {"Content-Type": "application/json"}, conn=c)
check("join", s == 200 and "token" in j)
tok = j["token"]
s, st = call(PUBLIC, "GET", f"/api/state?v=0&token={tok}", conn=c)
check("keep-alive: andra anropet på samma anslutning", s == 200 and st["team"]["name"] == "Testlaget")
for _ in range(5):
    call(ADMIN, "POST", "/api/host/action", {"action": "next"}, H)
s, hv = call(ADMIN, "GET", "/api/host/state?v=0")
check("fråga 1 visas", hv["kind"] == "question")
s, _ = call(PUBLIC, "POST", "/api/answer", {"token": tok, "r": 0, "q": 0, "text": hv["question"]["correctOption"]},
            {"Content-Type": "application/json"}, conn=c)
check("svar sparas", s == 200)
check("fel alternativ nekas", call(PUBLIC, "POST", "/api/answer", {"token": tok, "r": 0, "q": 0, "text": "Hittepå"},
                                   {"Content-Type": "application/json"})[0] == 400)

# Publika porten får inte visa något annat
for p in ("/host", "/edit", "/api/admin/info", "/api/host/state", "/media/x.mp4", "/qmedia/exempelquiz/x", "/static/host.html"):
    check(f"publik port nekar {p}", call(PUBLIC, "GET", p)[0] == 404)
check("publik port nekar värdåtgärd", call(PUBLIC, "POST", "/api/host/action", {"action": "next"}, H)[0] == 404)

# Media, export och import
s, m = call(ADMIN, "PUT", "/api/admin/media?id=exempelquiz&name=L%C3%A5t%201.MP3", b"ID3data", {"X-Musikquiz": "1"})
check("uppladdning ger säkert filnamn", s == 200 and m["name"] == "lat-1.mp3")
check("okänd filtyp nekas", call(ADMIN, "PUT", "/api/admin/media?id=exempelquiz&name=x.exe", b"zz", {"X-Musikquiz": "1"})[0] == 400)
s, raw = call(ADMIN, "GET", "/api/admin/export?id=exempelquiz")
check("export ger zip", s == 200 and raw[:2] == b"PK")
bad = io.BytesIO()
with zipfile.ZipFile(bad, "w") as z:
    z.writestr("quiz.json", json.dumps({"title": "Ond", "rounds": []}))
    z.writestr("media/../../ond.mp3", b"x")
s, d = call(ADMIN, "PUT", "/api/admin/import", bad.getvalue(), {"X-Musikquiz": "1"})
check("import av zip med farliga sökvägar", s == 200 and not os.path.exists(os.path.join(app.lib.root, "ond.mp3")))
s, d = call(ADMIN, "PUT", "/api/admin/import", raw, {"X-Musikquiz": "1"})
check("exporterad fil går att importera", s == 200)

# Lagsymbol och spärrat slutresultat
s, j2 = call(PUBLIC, "POST", "/api/join", {"name": "Emojilaget", "emoji": "🦄"}, {"Content-Type": "application/json"})
check("vald lagsymbol sparas", j2.get("emoji") == "🦄")
s, j3 = call(PUBLIC, "POST", "/api/join", {"name": "Fusklaget", "emoji": "<script>"}, {"Content-Type": "application/json"})
check("okänd lagsymbol ersätts", j3.get("emoji") != "<script>" and j3.get("emoji"))
s, hv = call(ADMIN, "GET", "/api/host/state?v=0")
call(ADMIN, "POST", "/api/host/action", {"action": "goto", "step": len(hv["steps"]) - 2}, H)
s, ps = call(PUBLIC, "GET", f"/api/state?v=0&token={j2['token']}")
check("mobilen ser inte vinnaren före avslöjandet", ps["kind"] == "final" and ps.get("pending") and "scores" not in ps)
call(ADMIN, "POST", "/api/host/action", {"action": "reveal"}, H)
s, ps = call(PUBLIC, "GET", f"/api/state?v=0&token={j2['token']}")
check("mobilen ser resultatet efter avslöjandet", "scores" in ps and ps["scores"][0].get("emoji"))

# Quiz från AI: AI-markering och standardregler följer med
ai_quiz = {"title": "AI-quiz", "rounds": [{"title": "R1", "type": "video", "instruction": "Vem?", "questions": [
    {"ai": True, "options": ["Roxette", "Europe", "", ""], "song": "The Look"}]}]}
s, d = call(ADMIN, "POST", "/api/admin/quiz/new", {"title": "AI-quiz", "quiz": ai_quiz}, H)
check("skapa quiz från AI-svar", s == 200)
s, q = call(ADMIN, "GET", f"/api/admin/quiz?id={d['id']}")
check("AI-markering sparas", q["quiz"]["rounds"][0]["questions"][0].get("ai") is True)
check("standardregler läggs till", len(q["quiz"]["rules"]) >= 3)
check("låt utan fil räknas som problem", any("låt- eller videofilen saknas" in p for p in q["problems"]))
s, info = call(ADMIN, "GET", "/api/admin/info")
check("biblioteket räknar AI-frågor", next(x for x in info["quizzes"] if x["id"] == d["id"])["aiUnchecked"] == 1)

app.shutdown()
print("\nAllt OK" if not fails else f"\n{fails} fel")
sys.exit(1 if fails else 0)
