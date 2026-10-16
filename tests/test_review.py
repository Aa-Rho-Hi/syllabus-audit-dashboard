"""Faculty submit -> coordinator review, with local-file storage and a fake Google Sheet."""
import pytest
from fastapi.testclient import TestClient

from app import store as store_mod


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import main
    monkeypatch.setenv("REVIEW_PASSCODE", "letmein")
    monkeypatch.delenv("SUBMISSIONS_SHEET", raising=False)
    monkeypatch.delenv("DRIVE_FOLDER_LINK", raising=False)
    monkeypatch.setattr(main, "BASE", tmp_path)          # store goes to tmp
    (tmp_path / "sample_syllabi").symlink_to(main.Path(__file__).resolve().parent.parent / "sample_syllabi")
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setattr(main.llm, "API_KEY", None)
    return TestClient(main.app)


SUB = {"course_number": "ecen489", "course_name": "Wireless Security", "faculty_name": "Dr. Lee",
       "faculty_email": "lee@tamu.edu", "note": "New elective",
       "text": "Students will be able to:\n1. Understand WPA2.\n2. Analyze attacks on WPA3 handshakes."}
P = {"X-Review-Passcode": "letmein"}


def test_submit_and_review(client):
    r = client.post("/api/submit", json=SUB).json()
    assert r["ok"] and r["flagged"] == 1
    sid = r["id"]

    assert client.get("/api/review/submissions").status_code == 401
    assert client.get("/api/review/submissions", headers={"X-Review-Passcode": "nope"}).status_code == 401
    d = client.get("/api/review/submissions", headers=P).json()
    x = d["items"][0]
    assert x["id"] == sid and x["status"] == "pending" and x["course_number"] == "ECEN 489"
    assert x["objectives"] == ["Understand WPA2.", "Analyze attacks on WPA3 handshakes."]
    assert x["issues"] and "understand" in x["issues"][0]
    assert d["storage"] == "local file"

    r = client.post(f"/api/review/{sid}", json={"status": "changes_requested", "comment": ""}, headers=P).json()
    assert r["ok"] is False                                   # comment required
    r = client.post(f"/api/review/{sid}", json={"status": "changes_requested", "comment": "Replace Understand",
                                                "reviewer": "Coord"}, headers=P).json()
    assert r["ok"] and r["item"]["status"] == "changes_requested" and r["item"]["reviewer"] == "Coord"
    r = client.post(f"/api/review/{sid}", json={"status": "approved"}, headers=P).json()
    assert r["item"]["status"] == "approved"
    assert client.post("/api/review/NOPE", json={"status": "approved"}, headers=P).status_code == 404


def test_submit_existing_course_includes_catalog(client):
    r = client.post("/api/submit", json={**SUB, "course_number": "CSCE 689", "text": "Explain TLS.\nDesign a protocol."}).json()
    assert r["ok"] and r["flagged"] == 0
    x = client.get("/api/review/submissions", headers=P).json()["items"][0]
    assert x["catalog_file"] == "CSCE689_Syllabus_Fall2025.docx" and len(x["catalog_objectives"]) == 5


def test_submit_validation(client):
    assert client.post("/api/submit", json={**SUB, "faculty_email": "nope"}).json()["ok"] is False
    assert client.post("/api/submit", json={**SUB, "course_number": "abc"}).json()["ok"] is False
    assert client.post("/api/submit", json={**SUB, "text": "\n\n"}).json()["ok"] is False


def test_review_disabled_without_passcode(client, monkeypatch):
    monkeypatch.delenv("REVIEW_PASSCODE")
    assert client.get("/api/review/submissions", headers=P).status_code == 503


# ---- fake Google Sheet -------------------------------------------------------
class _Exec:
    def __init__(self, f): self.f = f
    def execute(self): return self.f()


class FakeSheet:
    def __init__(self): self.rows, self.tabs = [], ["Sheet1"]
    # spreadsheets()
    def get(self, spreadsheetId, fields=None, range=None):
        if range is None:
            return _Exec(lambda: {"sheets": [{"properties": {"title": t}} for t in self.tabs]})
        if range.endswith("A1:Q1"):
            return _Exec(lambda: {"values": self.rows[:1]})
        return _Exec(lambda: {"values": self.rows[1:]})
    def batchUpdate(self, spreadsheetId, body):
        return _Exec(lambda: self.tabs.append(body["requests"][0]["addSheet"]["properties"]["title"]))
    # values()
    def update(self, spreadsheetId, range, valueInputOption, body):
        def run():
            n = int(range.split("!A")[1]) - 1
            while len(self.rows) <= n: self.rows.append([])
            self.rows[n] = body["values"][0]
        return _Exec(run)
    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        return _Exec(lambda: self.rows.append(body["values"][0]))


def test_sheet_store(monkeypatch):
    fake = FakeSheet()

    class Svc:
        def spreadsheets(self):
            class S:
                def values(_): return fake
                def get(_, **kw): return fake.get(**kw)
                def batchUpdate(_, **kw): return fake.batchUpdate(**kw)
            return S()
    import googleapiclient.discovery
    monkeypatch.setattr(googleapiclient.discovery, "build", lambda *a, **k: Svc())
    from app import sources
    monkeypatch.setattr(sources, "_service_account_creds", lambda scopes=None: object())

    st = store_mod.SheetStore("x" * 30)
    assert "Submissions" in fake.tabs and fake.rows[0] == store_mod.COLUMNS
    st.add({"id": "AB12", "status": "pending", "objectives": ["Explain A.", "Design B."], "flagged": 0, "ai_checked": True})
    got = st.all()
    assert got[0]["objectives"] == ["Explain A.", "Design B."] and got[0]["ai_checked"] is True
    d = st.update("AB12", {"status": "approved", "reviewer": "C"})
    assert d["status"] == "approved" and st.all()[0]["reviewer"] == "C"
    assert st.update("ZZ", {"status": "approved"}) is None


def test_parse_sheet_id():
    assert store_mod.parse_sheet_id("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=0") == "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert store_mod.parse_sheet_id("hello") is None
