"""The web app's Simple Syllabus tab: local-only, file serving locked to syllabi/, one job at a time."""
import time

import pytest
from fastapi.testclient import TestClient

from app import main, simplesyllabus_job as ss


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "SYLLABI", tmp_path / "syllabi")
    monkeypatch.delenv("K_SERVICE", raising=False)
    ss.JOB.reset()
    return TestClient(main.app)


def fake_pull(args, out, progress=print, on_total=None):
    out.mkdir(parents=True)
    progress("Logged in as Test.")
    on_total(1)
    term = out / "Spring 2027"
    (term / "_data").mkdir(parents=True)
    (term / "_data" / "ECEN_314_200_x.json").write_text(
        '{"code":"x","title":"ECEN 314 200 (1)","status":"awaiting_approval","source":"draft","components":'
        '[{"html":"<h2 class=\\"component-name\\">Course Learning Outcomes</h2><ul><li>Understand signals fully</li></ul>"}]}')
    (term / "ECEN_314_200_1_x.pdf").write_bytes(b"%PDF-1.4 test")
    progress("  [1/1] ECEN 314 200: 1 objectives")
    return [{"course": "ECEN 314 200", "note": "1 objectives"}]


def wait_done(client):
    for _ in range(100):
        s = client.get("/api/ss/status").json()
        if s["state"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_full_run_and_files(client, monkeypatch):
    import reviewer.pull
    monkeypatch.setattr(reviewer.pull, "run_pull", fake_pull)
    assert client.get("/api/config").json()["simple_syllabus"] is True
    assert client.post("/api/ss/run", json={"subject": "ECEN"}).status_code == 200
    s = wait_done(client)
    assert s["state"] == "done", s
    assert s["results"][0]["objectives"][0]["status"] == "avoid"
    pdf = s["results"][0]["file"]["link"]
    assert client.get(pdf).headers["content-type"] == "application/pdf"
    assert client.get(s["excel"]).status_code == 200


@pytest.mark.parametrize("path", ["../app/main.py", "../../etc/passwd", "/etc/passwd", "x/../../.env",
                                  "2026/Spring/_data/a.json", "%2e%2e/app/main.py"])
def test_file_endpoint_stays_in_syllabi(client, path):
    assert client.get("/api/ss/file", params={"path": path}).status_code == 404


def test_only_one_job_at_a_time(client, monkeypatch):
    import reviewer.pull

    def slow(*a, **k):
        time.sleep(0.5)
        return []
    monkeypatch.setattr(reviewer.pull, "run_pull", slow)
    assert client.post("/api/ss/run", json={}).status_code == 200
    assert client.post("/api/ss/run", json={}).status_code == 409
    assert wait_done(client)["state"] == "done"


def test_inputs_are_cleaned(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(ss.JOB, "start", lambda *a: seen.setdefault("a", a) and True)
    client.post("/api/ss/run", json={"subject": "ec-en; rm -rf /", "term": "Spring 2027<script>",
                                     "status": "evil", "courses": "314, 403; DROP"})
    subject, term, status, courses, _ = seen["a"]
    assert subject == "ECENRM" and term == "Spring 2027script" and status == "awaiting_approval"
    assert courses == ["314", "403"]


def test_off_on_cloud_run(client, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "syllabus-audit")
    assert client.get("/api/config").json()["simple_syllabus"] is False
    assert client.post("/api/ss/run", json={}).status_code == 403
    assert client.get("/api/ss/status").status_code == 403


def test_only_from_this_computer(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "SYLLABI", tmp_path)
    remote = TestClient(main.app, client=("10.0.0.5", 5555))
    assert remote.get("/api/ss/status").status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example"])
def test_other_site_names_blocked(client, host):
    # DNS rebinding: the request comes from this computer but is addressed to another site's name.
    (ss.SYLLABI / "a").mkdir(parents=True)
    (ss.SYLLABI / "a" / "r.pdf").write_bytes(b"%PDF-1.4")
    h = {"Host": host}
    assert client.get("/api/ss/status", headers=h).status_code == 403
    assert client.get("/api/ss/file", params={"path": "a/r.pdf"}, headers=h).status_code == 403
    assert client.post("/api/ss/run", json={}, headers=h).status_code == 403
    assert client.get("/api/ss/file", params={"path": "a/r.pdf"}, headers={"Host": "localhost:8000"}).status_code == 200


@pytest.mark.parametrize("kw", [{}, {"data": {"a": "1"}}, {"content": "{}", "headers": {"Content-Type": "text/plain"}}])
def test_posts_need_json(client, monkeypatch, kw):
    # Other sites can send these without asking the browser first.
    monkeypatch.setattr(ss.JOB, "start", lambda *a: pytest.fail("job started"))
    monkeypatch.setattr(ss, "open_folder", lambda *a: pytest.fail("folder opened"))
    assert client.post("/api/ss/open-folder", **kw).status_code == 415
    assert client.post("/api/ss/run", **kw).status_code in (415, 422)  # FastAPI may reject the body first


def test_my_todo_column_option(monkeypatch):
    seen = []
    monkeypatch.setattr(ss.Job, "_run", lambda self, args, use_llm: seen.append(args))
    job = ss.Job()
    assert job.start("ECEN", "", "mine", [], False)
    assert seen[0].mine is True and seen[0].status == "awaiting_approval"
    job.reset()
    assert job.start("ECEN", "", "completed", [], False)
    assert seen[1].mine is False and seen[1].status == "completed"
