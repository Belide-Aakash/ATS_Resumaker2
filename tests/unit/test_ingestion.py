"""RI ingestion tests: onboarding slug/ATS parsing (deterministic) + service dedupe
(fake source, isolated tmp DB). No network."""
from __future__ import annotations

import json

import pytest

from resumaker.config import Settings
from resumaker.domain import BoardRef, Company
from resumaker.ingestion import onboard, service
from resumaker.providers.sources import available_sources, get_source
from resumaker.providers.sources.base import PostingStub


def test_sources_registered():
    # Do NOT pin the exact adapter set: new adapters get added over time — including ones the
    # onboarding agent auto-drafts and PRs — so a hardcoded list would break on every addition.
    # Instead assert a stable core is present and that every registered adapter is well-formed
    # (its registry key matches `.source` and it exposes `list_postings`).
    sources = set(available_sources())
    assert {"greenhouse", "lever", "ashby", "workday"} <= sources
    for name in sources:
        adapter = get_source(name)
        assert adapter.source == name, f"{name}: registry key != adapter.source ({adapter.source})"
        assert callable(getattr(adapter, "list_postings", None)), f"{name}: no list_postings"


def test_slug_candidates():
    c = onboard.slug_candidates("State Street")
    assert "statestreet" in c and "state-street" in c
    assert "state" not in c                               # bare first word dropped (anti-false-positive)
    c2 = onboard.slug_candidates("JPMC - Chase")
    assert "jpmc" in c2 and "chase" in c2                 # split on ' - '
    assert "bytedance" in onboard.slug_candidates("ByteDance/TikTok")
    assert all(len(s) >= 4 for s in c)                    # min-length guard


@pytest.mark.parametrize("html,source,token", [
    ('<a href="https://boards.greenhouse.io/databricks/jobs/1">', "greenhouse", "databricks"),
    ('<a href="https://jobs.lever.co/netflix">', "lever", "netflix"),
    ('<a href="https://jobs.ashbyhq.com/notion">', "ashby", "notion"),
])
def test_board_from_html(html, source, token):
    b = onboard.board_from_html(html)
    assert b and b.source == source and b.token == token


def test_board_from_html_workday():
    b = onboard.board_from_html('<iframe src="https://statestreet.wd1.myworkdayjobs.com/en-US/Global">')
    assert b and b.source == "workday" and b.token == "statestreet"
    assert b.extra["host"] == "statestreet.wd1.myworkdayjobs.com" and b.extra["site"] == "Global"


def test_board_from_html_none():
    assert onboard.board_from_html("<p>no ATS here</p>") is None


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    s = Settings(root_dir=tmp_path, data_dir=tmp_path / "data")
    for mod in ("resumaker.persistence.db", "resumaker.persistence.cache",
                "resumaker.config.settings"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: s)
    from resumaker.persistence import db
    db.init_db()
    return s


def test_service_dedupes_on_reingest(tmp_db, monkeypatch):
    stubs = [PostingStub(source="greenhouse", external_id="1", title="ML Engineer",
                         location="Boston", updated_at="2026-01-01"),
             PostingStub(source="greenhouse", external_id="2", title="Data Scientist",
                         location="NYC", updated_at="2026-01-02")]

    class Fake:
        source = "greenhouse"
        def list_postings(self, token, **kw):
            return stubs

    monkeypatch.setattr(service, "get_source", lambda name: Fake())
    company = Company(name="Acme", boards=[BoardRef(source="greenhouse", token="acme")])

    r1 = service.ingest_company(company)
    assert r1.new == 2 and r1.unchanged == 0 and len(r1.new_jobs) == 2
    assert r1.new_jobs[0].posted_at == "2026-01-01"        # posted date captured

    r2 = service.ingest_company(company)                    # re-ingest, nothing changed
    assert r2.new == 0 and r2.unchanged == 2

    stubs[0].title = "Senior ML Engineer"                   # edited posting -> changed
    r3 = service.ingest_company(company)
    assert r3.new == 1 and r3.unchanged == 1


def test_ingest_routes_a_failed_board_to_errors_not_zero(tmp_db, monkeypatch):
    """EMPTY vs ERROR (the exact discipline the course grades): a board whose fetch
    RAISES must land in `res.errors` as a value - it must never silently become "0 jobs"."""
    class Boom:
        source = "greenhouse"
        def list_postings(self, token, **kw):
            raise RuntimeError("HTTP 403 forbidden")

    monkeypatch.setattr(service, "get_source", lambda name: Boom())
    company = Company(name="Acme", boards=[BoardRef(source="greenhouse", token="acme")])

    res = service.ingest_company(company)
    assert res.new == 0                              # nothing recorded
    assert len(res.errors) == 1                      # the failure is a value, not a 0
    assert "greenhouse/acme" in res.errors[0]        # provenance: which board failed
    assert "403" in res.errors[0]


def test_ingest_treats_an_empty_board_as_empty_not_error(tmp_db, monkeypatch):
    """A board that fetches successfully but returns [] is EMPTY, not an ERROR: it must
    NOT pollute `res.errors`. An empty errors list is the signal the sweep worked and the
    board genuinely had nothing - the opposite of a silent failure masquerading as 0."""
    class Empty:
        source = "greenhouse"
        def list_postings(self, token, **kw):
            return []

    monkeypatch.setattr(service, "get_source", lambda name: Empty())
    company = Company(name="Acme", boards=[BoardRef(source="greenhouse", token="acme")])

    res = service.ingest_company(company)
    assert res.new == 0
    assert res.errors == []                          # empty != error


def test_one_bad_board_does_not_sink_its_siblings(tmp_db, monkeypatch):
    """Failure isolation: one board raising routes to `errors` and the sweep keeps going,
    so a sibling board still ingests. One bad board must not sink the rest."""
    good = [PostingStub(source="greenhouse", external_id="1", title="ML Engineer",
                        location="Boston", updated_at="2026-01-01")]

    class Mixed:
        def list_postings(self, token, **kw):
            if token == "bad":
                raise RuntimeError("connection reset")
            return good

    monkeypatch.setattr(service, "get_source", lambda name: Mixed())
    company = Company(name="Acme", boards=[
        BoardRef(source="greenhouse", token="bad"),
        BoardRef(source="greenhouse", token="good"),
    ])

    res = service.ingest_company(company)
    assert res.new == 1                              # the good board still ingested
    assert len(res.errors) == 1 and "greenhouse/bad" in res.errors[0]


def test_ingest_all_skips_companies_off_the_selected_sources(tmp_db, monkeypatch):
    # A narrowed sweep (per-cadence polling) must not even touch companies whose boards are
    # all on other ATSs - otherwise the fast tick's wall-time scales with the whole watchlist
    # and blows past the Cloud Scheduler attempt deadline.
    from resumaker.persistence import db
    db.add_company(Company(name="FastCo", boards=[BoardRef(source="greenhouse", token="fastco")]))
    db.add_company(Company(name="SlowCo", boards=[BoardRef(source="workday", token="slowco")]))

    polled: list[str] = []

    class Fake:
        def list_postings(self, token, **kw):
            polled.append(token)
            return []

    monkeypatch.setattr(service, "get_source", lambda name: Fake())
    monkeypatch.setattr(service.time, "sleep", lambda *_: None)

    service.ingest_all(sources={"greenhouse"})
    assert polled == ["fastco"]                             # SlowCo skipped entirely


def test_upsert_jobs_bulk_new_changed_unchanged(tmp_db):
    # The batched writer classifies each posting correctly and (crucially) does NOT rewrite
    # unchanged rows one-by-one - it returns changed=False for them.
    from resumaker.domain import JobRecord
    from resumaker.persistence import db

    def rec(eid, title, ch):
        return JobRecord(source="greenhouse", external_id=eid, title=title, company="Acme",
                         url=f"https://x/{eid}", content_hash=ch)

    r1 = db.upsert_jobs_bulk([rec("1", "AI Engineer", "h1"), rec("2", "ML Engineer", "h2")])
    assert [c for _, c in r1] == [True, True]              # both new
    assert len(db.list_jobs()) == 2

    # re-poll: id 1 unchanged, id 2 content changed -> [unchanged, changed]
    r2 = db.upsert_jobs_bulk([rec("1", "AI Engineer", "h1"), rec("2", "ML Engineer II", "h2b")])
    assert [c for _, c in r2] == [False, True]
    assert r2[0][0] == r1[0][0]                            # same id, no new row
    assert len(db.list_jobs()) == 2                        # still 2 (no duplicates)


def test_ingest_all_concurrent_across_sources_is_correct(tmp_db, monkeypatch):
    # Boards on different sources fetch concurrently (grouped by host); the serial DB-write
    # phase must still record every posting exactly once, with no cross-thread write races.
    from resumaker.persistence import db
    db.add_company(Company(name="Acme", boards=[BoardRef(source="greenhouse", token="acme")]))
    db.add_company(Company(name="Globex", boards=[BoardRef(source="lever", token="globex")]))

    def stubs_for(src):
        return [PostingStub(source=src, external_id=f"{src}-{i}", title="ML Engineer",
                            location="Boston", updated_at="2026-01-0" + str(i)) for i in (1, 2)]

    class Fake:
        def __init__(self, src): self.src = src
        def list_postings(self, token, **kw): return stubs_for(self.src)

    monkeypatch.setattr(service, "get_source", lambda name: Fake(name))
    monkeypatch.setattr(service.time, "sleep", lambda *_: None)

    results = service.ingest_all()
    by_co = {r.company: r for r in results}
    assert by_co["Acme"].new == 2 and by_co["Globex"].new == 2
    assert len(db.list_jobs()) == 4                         # every posting persisted once

    r2 = {r.company: r for r in service.ingest_all()}       # idempotent re-ingest
    assert r2["Acme"].new == 0 and r2["Acme"].unchanged == 2


def test_discovery_filters_and_facets(tmp_db, monkeypatch):
    from resumaker.domain import JobRecord
    from resumaker.ingestion import DiscoveryFilters, discover
    from resumaker.persistence import db
    rows = [
        ("greenhouse", "1", "Machine Learning Engineer", "Anthropic", "San Francisco, CA"),
        ("greenhouse", "2", "Data Engineer", "Anthropic", "New York, NY"),
        ("ashby", "3", "Security Engineer", "OpenAI", "Remote, US"),
        ("workday", "4", "Staff Software Engineer", "NVIDIA", "Santa Clara, CA"),
    ]
    for src, ext, title, co, loc in rows:
        db.upsert_job(JobRecord(source=src, external_id=ext, title=title, company=co,
                                location=loc, content_hash=ext))

    # unfiltered
    r = discover(DiscoveryFilters())
    assert r.total == 4 and len(r.jobs) == 4
    assert r.facets["companies"]["Anthropic"] == 2

    # company filter
    assert discover(DiscoveryFilters(company=["Anthropic"])).total == 2
    # source filter
    assert discover(DiscoveryFilters(source="ashby")).total == 1
    # location substring
    assert discover(DiscoveryFilters(location="ca")).total == 2   # SF + Santa Clara
    # keyword matches title OR company
    assert discover(DiscoveryFilters(keyword="engineer")).total == 4
    assert discover(DiscoveryFilters(keyword="data")).total == 1
    assert discover(DiscoveryFilters(keyword="nvidia")).total == 1   # company match
    # multi-select company / level
    assert discover(DiscoveryFilters(company=["Anthropic", "OpenAI"])).total == 3
    assert discover(DiscoveryFilters(level=["senior", "staff"])).total == 1  # 'Staff Software Engineer'
    # pagination
    assert len(discover(DiscoveryFilters(limit=2)).jobs) == 2


def test_tracker_add_runs_match_and_lifecycle(tmp_db, monkeypatch):
    from resumaker.domain import JobRecord
    from resumaker.ingestion import tracker
    from resumaker.persistence import db

    jid, _ = db.upsert_job(JobRecord(source="greenhouse", external_id="1",
                                     title="ML Engineer", company="Anthropic",
                                     location="SF, CA", url="https://x/jobs/1", content_hash="1"))

    # stub the match pipeline (no network/LLM) with a PipelineResult-like object
    from types import SimpleNamespace
    res = SimpleNamespace(
        error="",
        job=SimpleNamespace(company="Anthropic", title="Machine Learning Engineer"),
        fit=SimpleNamespace(final_0_100=78.0),
        decision=SimpleNamespace(recommend_apply=True),
        sponsorship={"verdict": "likely"},
        out_dir="/tmp/outputs/anthropic-mle")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: res)

    e = tracker.add(job_id=jid)
    assert e.stage == "interested" and e.fit_0_100 == 78.0 and e.recommend_apply is True
    assert e.sponsorship == "likely" and e.run_id == "anthropic-mle"
    assert e.title == "ML Engineer"                    # the ATS posting title is kept, not the
    assert e.company == "Anthropic"                    # JD-extracted one (which can differ/mislead)

    # lifecycle
    assert tracker.set_stage(e.id, "applied").stage == "applied"
    with pytest.raises(tracker.TrackerError):
        tracker.set_stage(e.id, "bogus")
    tracker.set_notes(e.id, "referred by X")
    assert db.get_tracker(e.id).notes == "referred by X"

    # re-add refreshes match but preserves stage + notes (keyed on url)
    e2 = tracker.add(job_id=jid)
    assert e2.id == e.id and e2.stage == "applied" and e2.notes == "referred by X"

    assert len(tracker.list_tracked()) == 1
    assert len(tracker.list_tracked(stage="applied")) == 1
    assert len(tracker.list_tracked(stage="interested")) == 0


def test_tracker_remove_cascades_run_and_artifacts(tmp_db, monkeypatch):
    """Deleting a tracked job cascades: its run folder (via the artifact store) AND the run's DB
    index row are removed, so nothing is left orphaned."""
    from types import SimpleNamespace

    from resumaker.domain import JobRecord, RunRecord
    from resumaker.ingestion import tracker
    from resumaker.persistence import db

    jid, _ = db.upsert_job(JobRecord(source="greenhouse", external_id="9", title="AI Engineer",
                                     company="Acme", url="https://x/jobs/9", content_hash="9"))
    res = SimpleNamespace(error="", job=SimpleNamespace(company="Acme", title="AI Engineer"),
                          fit=SimpleNamespace(final_0_100=70.0),
                          decision=SimpleNamespace(recommend_apply=True),
                          sponsorship={"verdict": "none"}, out_dir="/tmp/o/acme-ai")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: res)

    deleted: list[str] = []
    monkeypatch.setattr("resumaker.persistence.artifacts.get_artifact_store",
                        lambda: SimpleNamespace(publish=lambda rid: None,
                                                delete_run=lambda rid: deleted.append(rid)))

    e = tracker.add(job_id=jid)
    rid = e.run_id
    db.record_run(RunRecord(id=rid, url="https://x/jobs/9", out_dir="/tmp/o/acme-ai",
                            status="matched"))
    assert db.get_run(rid) is not None

    assert tracker.remove(e.id) == 1
    assert deleted == [rid]                 # artifacts cascade-deleted
    assert db.get_run(rid) is None          # run index row cascade-deleted
    assert db.get_tracker(e.id) is None     # tracker row gone


def test_tracker_lookup_by_run_id(tmp_db, monkeypatch):
    """get_by_run resolves a match run_id back to its tracked entry (authoritative ATS title)."""
    from types import SimpleNamespace

    from resumaker.domain import JobRecord
    from resumaker.ingestion import tracker
    from resumaker.persistence import db

    jid, _ = db.upsert_job(JobRecord(source="greenhouse", external_id="7", title="AI Engineer",
                                     company="Acme", url="https://x/jobs/7", content_hash="7"))
    res = SimpleNamespace(error="", job=SimpleNamespace(company="Acme", title="Software Engineering III"),
                          fit=SimpleNamespace(final_0_100=71.0),
                          decision=SimpleNamespace(recommend_apply=True),
                          sponsorship={"verdict": "likely"}, out_dir="/tmp/o/acme-ai7")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: res)

    e = tracker.add(job_id=jid)
    assert e.title == "AI Engineer"                   # ATS title kept over the JD "Software Engineering III"
    found = tracker.get_by_run(e.run_id)
    assert found is not None and found.id == e.id and found.title == "AI Engineer"
    assert tracker.get_by_run("no-such-run") is None


def test_quiet_hours_enabled_flag_and_default_window(tmp_db):
    """quiet_enabled=False is never quiet (email 24/7); an enabled-but-empty window inherits the
    overnight 00:00-08:00 default so it applies without a manual re-save."""
    from resumaker.ingestion.notify import _in_quiet_hours
    from resumaker.persistence import db
    from resumaker.persistence.profile import load_mailer_prefs

    # disabled -> never quiet even inside a configured window
    assert _in_quiet_hours({"quiet_enabled": False, "quiet_start": "00:00",
                            "quiet_end": "08:00", "timezone": "UTC"}) is False

    # legacy doc: quiet enabled (default) but empty window -> coalesced to the overnight default
    db.put_document("mailer_prefs", {"quiet_start": "", "quiet_end": ""})
    mp = load_mailer_prefs()
    assert mp["quiet_enabled"] is True and (mp["quiet_start"], mp["quiet_end"]) == ("00:00", "08:00")

    # explicitly disabled -> window left untouched, never quiet
    db.put_document("mailer_prefs", {"quiet_enabled": False, "quiet_start": "", "quiet_end": ""})
    mp2 = load_mailer_prefs()
    assert mp2["quiet_enabled"] is False and _in_quiet_hours(mp2) is False


def test_tracker_raw_url_add_fills_title_from_jd(tmp_db, monkeypatch):
    # A raw-URL add has no watchlist title/company, so the JD-extracted fields fill them in.
    from types import SimpleNamespace

    from resumaker.ingestion import tracker
    res = SimpleNamespace(
        error="", job=SimpleNamespace(company="Stripe", title="Full Stack Engineer"),
        fit=SimpleNamespace(final_0_100=60.0), decision=SimpleNamespace(recommend_apply=True),
        sponsorship={"verdict": "unclear"}, out_dir="/tmp/outputs/stripe-fse")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: res)

    e = tracker.add(url="https://stripe.com/jobs/123")
    assert e.title == "Full Stack Engineer" and e.company == "Stripe"


def test_rematch_refreshes_title_from_watchlist(tmp_db, monkeypatch):
    # A re-match must CORRECT a stale/wrong stored title back to the ATS listing title (the fix
    # for entries whose title was clobbered by an earlier buggy JD-extracted match).
    from types import SimpleNamespace

    from resumaker.domain import JobRecord
    from resumaker.ingestion import tracker
    from resumaker.persistence import db

    jid, _ = db.upsert_job(JobRecord(source="workday", external_id="1", title="AI Engineer",
                                     company="Morgan Stanley", url="https://ms/1", content_hash="1"))
    e = tracker.add(job_id=jid, run_match=False)
    db.set_tracker_stage(e.id, "interested")
    # simulate the old bug: the row got a wrong title from a JD-extracted match
    e.title = "Software Engineering III"
    db.upsert_tracker(e)

    res = SimpleNamespace(error="", job=SimpleNamespace(company="Morgan Stanley", title="whatever"),
                          fit=SimpleNamespace(final_0_100=74.0),
                          decision=SimpleNamespace(recommend_apply=True),
                          sponsorship={"verdict": "likely"}, out_dir="/tmp/outputs/ms-ai")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: res)

    tracker.run_match_for(e.id)
    assert db.get_tracker(e.id).title == "AI Engineer"     # corrected from the watchlist


def test_tracker_add_requires_target(tmp_db):
    from resumaker.ingestion import tracker
    with pytest.raises(tracker.TrackerError):
        tracker.add(run_match=False)


def test_tracker_match_failure_sets_error_then_retry_clears(tmp_db, monkeypatch):
    """A failed match records `match_error` (not an eternal 'matching…'); a later successful
    rematch clears it and fills in fit/decision."""
    from types import SimpleNamespace

    from resumaker.domain import JobRecord
    from resumaker.ingestion import tracker
    from resumaker.persistence import db

    jid, _ = db.upsert_job(JobRecord(source="oracle_cloud", external_id="9",
                                     title="Data Engineer", company="JPMC",
                                     location="TX", url="https://x/job/9", content_hash="9"))

    failed = SimpleNamespace(error="RuntimeError: all extraction passes failed",
                             job=None, fit=None, decision=None, sponsorship=None, out_dir="")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: failed)
    e = tracker.add(job_id=jid)
    assert e.fit_0_100 is None and e.match_error and "extraction" in e.match_error

    # retry with a now-succeeding pipeline clears the error and populates the match
    ok = SimpleNamespace(error="",
                         job=SimpleNamespace(company="JPMorgan Chase", title="Data Engineer III"),
                         fit=SimpleNamespace(final_0_100=44.1),
                         decision=SimpleNamespace(recommend_apply=False),
                         sponsorship={"verdict": "likely"}, out_dir="/tmp/outputs/jpmc-de")
    monkeypatch.setattr("resumaker.pipeline.run_pipeline", lambda **kw: ok)
    cleared = tracker.clear_match_error(e.id)
    assert cleared.match_error is None
    tracker.run_match_for(e.id)
    got = db.get_tracker(e.id)
    assert got.match_error is None and got.fit_0_100 == 44.1 and got.run_id == "jpmc-de"


def test_title_matches_include_exclude():
    """Shared title gate: contains ANY include word AND NONE of the exclude words."""
    from resumaker.ingestion.service import title_matches
    # want AI, drop Java/Manager
    assert title_matches("AI Engineer", include=["ai"], exclude=["java", "manager"])
    assert not title_matches("Java AI Engineer", include=["ai"], exclude=["java"])   # excluded
    assert not title_matches("Engineering Manager", include=["ai"], exclude=["manager"])
    assert not title_matches("Backend Engineer", include=["ai"], exclude=[])         # missing include
    assert title_matches("Backend Engineer", include=[], exclude=["java"])           # no include req
    assert title_matches("ML Engineer", include=["ai", "ml"], exclude=[])            # ANY include


def test_discovery_title_filter(tmp_db, monkeypatch):
    from resumaker.domain import JobRecord
    from resumaker.ingestion import DiscoveryFilters, discover
    from resumaker.persistence import db

    for i, title in enumerate(["AI Engineer", "Java AI Engineer", "ML Engineer", "Data Analyst"]):
        db.upsert_job(JobRecord(source="greenhouse", external_id=str(i), title=title,
                                company="Acme", location="Boston, MA", content_hash=str(i)))
    res = discover(DiscoveryFilters(title_include=["engineer"], title_exclude=["java"], limit=50))
    titles = {j.title for j in res.jobs}
    assert titles == {"AI Engineer", "ML Engineer"}          # engineer, not java, not analyst


def test_mailer_filter_narrows_pending(tmp_db):
    """The email digest applies the owner's mailer title filter (include/exclude) on top of the
    on-target gate; default empty = no change."""
    from resumaker.domain import JobRecord
    from resumaker.ingestion import notify
    from resumaker.persistence import db, profile

    for i, t in enumerate(["AI Engineer", "Java Engineer"]):
        db.upsert_job(JobRecord(source="greenhouse", external_id=str(i), title=t,
                                company="Acme", content_hash=str(i)))
    jobs = db.list_jobs()
    assert {j.title for j in notify.pending(jobs)} == {"AI Engineer", "Java Engineer"}  # no filter

    profile.save_mailer_filter({"include": ["ai"], "exclude": ["java"]})
    assert {j.title for j in notify.pending(jobs)} == {"AI Engineer"}   # filtered


def test_mailer_preview_counts(tmp_db):
    """The Mailer page's live 'X of N' reflects the same predicate as the digest, over the whole
    jobs table, and applies the max-postings cap to `would_send`."""
    from resumaker.domain import JobRecord
    from resumaker.ingestion import notify
    from resumaker.persistence import db

    for i, t in enumerate(["AI Engineer", "ML Engineer", "Java Engineer", "Recruiter"]):
        db.upsert_job(JobRecord(source="greenhouse", external_id=str(i), title=t,
                                company="Acme", content_hash=str(i)))

    # no filter: on_target = the 3 tech titles (Recruiter isn't on-target); all match; no cap
    base = notify.preview_counts({})
    assert base["on_target"] == 3 and base["matching"] == 3
    assert base["cap"] == 0 and base["would_send"] == 3

    # narrow to "ai"/"ml", cap at 1 -> matches 2, would send 1 of 2
    p = {"include": ["ai", "ml"], "exclude": ["java"], "max_postings": 1}
    got = notify.preview_counts(p)
    assert got["matching"] == 2 and got["cap"] == 1 and got["would_send"] == 1
    assert got["on_target"] == 3                         # denominator unaffected by the filter


def test_mailer_frequency_sync_noop_off_cloud(monkeypatch):
    """Off-cloud (no GCP project) the Cloud Scheduler sync is a no-op."""
    from types import SimpleNamespace

    from resumaker.ingestion import schedule_sync
    monkeypatch.setattr(schedule_sync, "get_settings",
                        lambda: SimpleNamespace(gcp_project=None, gcp_region="us-central1",
                                                mailer_scheduler_job="resumaker-mailer"))
    assert schedule_sync.sync_mailer_frequency("hourly") == "skipped (no gcp)"


def test_email_pending_uses_full_backlog(tmp_db, monkeypatch):
    """email_pending emails the whole unnotified on-target backlog, not just a single tick's new
    jobs — so a posting deferred earlier (quiet hours / low frequency) still goes out later."""
    from resumaker.domain import JobRecord
    from resumaker.ingestion import notify
    from resumaker.persistence import db

    for i, t in enumerate(["AI Engineer", "ML Engineer", "Office Manager"]):
        db.upsert_job(JobRecord(source="greenhouse", external_id=str(i), title=t,
                                company="Acme", url=f"https://x/{i}", content_hash=str(i)))
    sent = {}
    monkeypatch.setattr(notify, "email_new", lambda jobs, **kw: sent.setdefault("n", len(jobs)))

    notify.email_pending()
    # both on-target titles are queued from the table (Office Manager is off-target, dropped)
    assert sent["n"] == 3   # email_new receives the full jobs table; its own pending() narrows


def test_actions_agent_runner_dispatch_poll_artifact(monkeypatch):
    """ActionsAgentRunner: dispatch the workflow, find the run by run-name, then download the
    contract artifact - all mocked (no GitHub calls). Fits the synchronous resolve() seam."""
    import io
    import json
    import zipfile

    from resumaker.config import get_settings
    from resumaker.onboarding import agent_runner as ar

    for k, v in {"RESUMAKER_ONBOARD_AGENT_ENABLED": "true", "RESUMAKER_ONBOARD_RUNNER": "actions",
                 "RESUMAKER_GITHUB_REPO": "me/repo", "RESUMAKER_GITHUB_TOKEN": "ghp_x"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    monkeypatch.setattr("time.sleep", lambda *_: None)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("contract.json", json.dumps(
            {"status": "resolved", "board": {"source": "greenhouse", "token": "acme"},
             "cost_usd": 0.1, "turns": 3}))
    zip_bytes = buf.getvalue()

    class Resp:
        def __init__(self, js=None, content=b""): self._js, self.content = js, content
        def raise_for_status(self): pass
        def json(self): return self._js

    class FakeHTTP:
        def post(self, url, json=None): return Resp()  # dispatch -> 204
        def get(self, url, params=None, follow_redirects=False):
            if url.endswith("/actions/runs"):
                return Resp({"workflow_runs": [
                    {"name": "onboard-r1", "id": 99, "status": "completed", "conclusion": "success"}]})
            if url.endswith("/artifacts"):
                return Resp({"artifacts": [{"name": "contract-r1", "archive_download_url": "dl"}]})
            return Resp(content=zip_bytes)  # the artifact zip download

    runner = ar.get_agent_runner()
    assert isinstance(runner, ar.ActionsAgentRunner)   # config selected the Actions runner
    runner._http = FakeHTTP()
    runner._poll_s = 0
    got = runner.resolve("Acme", None, run_id="r1", on_event=lambda *a: None)
    assert got["status"] == "resolved" and got["board"]["source"] == "greenhouse"
    assert got["turns"] == 3
    get_settings.cache_clear()


def test_agent_runner_actions_requires_repo_and_token(monkeypatch):
    """Without github creds, actions mode falls back to Null (never crashes onboarding)."""
    from resumaker.config import get_settings
    from resumaker.onboarding import agent_runner as ar

    monkeypatch.setenv("RESUMAKER_ONBOARD_AGENT_ENABLED", "true")
    monkeypatch.setenv("RESUMAKER_ONBOARD_RUNNER", "actions")  # but no repo/token set
    get_settings.cache_clear()
    assert isinstance(ar.get_agent_runner(), ar.NullAgentRunner)
    get_settings.cache_clear()


def test_oracle_cloud_scraper(monkeypatch):
    """The oracle_cloud handler recognizes CE careers URLs and pulls the JD from the public
    requisition-detail JSON API (the JS page's own source), not the empty HTML shell."""
    import httpx

    from resumaker.providers.scrape import scraper

    detail = {"Title": "Data Engineer III", "PrimaryLocation": "Houston, TX",
              "ExternalDescriptionStr": "<p>Build <b>data</b> pipelines.</p>",
              "ExternalResponsibilitiesStr": "<ul><li>Own ETL</li></ul>"}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return detail

    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    url = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210759920"
    r = scraper._oracle_cloud(url)
    assert r is not None and r.source_type == "oracle_cloud"
    assert r.title == "Data Engineer III" and r.company == "jpmc"
    low = r.raw_text.lower()
    assert "pipelines" in low and "own etl" in low   # both JD fields stitched + de-HTML'd
    assert "recruitingCEJobRequisitionDetails/210759920" in seen["url"]
    # a non-oracle URL is not claimed by this handler
    assert scraper._oracle_cloud("https://boards.greenhouse.io/acme/jobs/1") is None


def test_amazon_scraper(monkeypatch):
    """The amazon handler recognizes amazon.jobs detail URLs and pulls the JD from the public
    search.json API (the SPA's own source), matching the exact job id and stitching the
    description + basic/preferred qualifications - not the empty HTML shell / 406 .json page."""
    import httpx

    from resumaker.providers.scrape import scraper

    body = {"jobs": [
        # a decoy the text-search may also return - must be ignored (id mismatch)
        {"id_icims": "999", "title": "Other Role", "description": "nope"},
        {"id_icims": "10496449", "title": "Software Development Engineer",
         "company_name": "Amazon.com Services LLC", "normalized_location": "Seattle, WA, USA",
         "description": "Build <b>conversational</b> ads.",
         "basic_qualifications": "3+ years <i>Java</i>.",
         "preferred_qualifications": "Experience with ML."},
    ]}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return body

    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["params"] = kw.get("params")
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    url = "https://www.amazon.jobs/en/jobs/10496449/software-development-engineer"
    r = scraper._amazon(url)
    assert r is not None and r.source_type == "amazon"
    assert r.title == "Software Development Engineer" and r.company == "Amazon.com Services LLC"
    low = r.raw_text.lower()
    assert "conversational" in low and "java" in low and "ml" in low   # all three JD fields, de-HTML'd
    assert "nope" not in low                                           # the id-mismatch decoy is dropped
    assert seen["params"]["base_query"] == "10496449"                  # queried by the exact job id
    # a non-amazon URL is not claimed by this handler
    assert scraper._amazon("https://boards.greenhouse.io/acme/jobs/1") is None


class _Resp:
    def __init__(self, *, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
    def raise_for_status(self): pass
    def json(self): return self._payload


def test_onboard_reports_already_on_watchlist(tmp_db):
    """Re-onboarding an existing company must say 'already on your watchlist', not a false 'added'."""
    from resumaker.domain import BoardRef, OnboardingRun
    from resumaker.onboarding import service

    board = BoardRef(source="greenhouse", token="acme")
    run1 = OnboardingRun(id="r1", name="Acme")
    service._resolve_and_add(run1, "deterministic", board, {"count": 5})
    assert run1.evidence["already_present"] is False
    assert "added to watchlist" in run1.events[-1].detail

    run2 = OnboardingRun(id="r2", name="acme")            # same company, different case
    service._resolve_and_add(run2, "deterministic", board, {"count": 5})
    assert run2.evidence["already_present"] is True
    assert "already on your watchlist" in run2.events[-1].detail


def test_workday_strips_apply_suffix(monkeypatch):
    """A .../job/<ext>/apply URL must query the CXS API for <ext>, not <ext>/apply."""
    from curl_cffi import requests as cffi

    from resumaker.providers.scrape import scraper
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return _Resp(payload={"jobPostingInfo": {"title": "Eng", "jobDescription": "<p>Build</p>",
                                                 "location": "OH"}})

    monkeypatch.setattr(cffi, "get", fake_get)
    url = "https://mitre.wd5.myworkdayjobs.com/MITRE/job/Fairborn-OH/Lead-Eng_R117203-1/apply"
    r = scraper._workday(url)
    assert r and r.source_type == "workday" and r.title == "Eng"
    assert seen["url"].endswith("Lead-Eng_R117203-1") and "/apply" not in seen["url"]


def test_greenhouse_custom_domain_derives_board_token(monkeypatch):
    """A custom-domain board (hubspot.com/...?gh_jid=) has no board token in the URL; derive it from
    the registrable domain and hit the public boards API."""
    from resumaker.providers.scrape import scraper
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return _Resp(payload={"title": "PM", "content": "<p>Own the board</p>",
                              "location": {"name": "Remote"}})

    monkeypatch.setattr(scraper.httpx, "get", fake_get)
    r = scraper._greenhouse("https://www.hubspot.com/careers/jobs/8119462?gh_jid=8119462")
    assert r and r.source_type == "greenhouse" and r.company == "hubspot"
    assert "/boards/hubspot/jobs/8119462" in seen["url"]


def test_rippling_scraper(monkeypatch):
    from resumaker.providers.scrape import scraper
    monkeypatch.setattr(scraper.httpx, "get", lambda url, **kw: _Resp(payload={
        "name": "AE", "description": "<p>Sell <b>things</b></p>",
        "workLocations": [{"label": "NYC"}]}))
    r = scraper._rippling("https://ats.rippling.com/rippling/jobs/2f0674e6-f01f-4ecd-b459-e947241c211f")
    assert r and r.source_type == "rippling" and "sell" in r.raw_text.lower() and "things" in r.raw_text.lower()
    assert scraper._rippling("https://jobs.lever.co/x/abc") is None


def test_eightfold_scraper(monkeypatch):
    from resumaker.providers.scrape import scraper
    monkeypatch.setattr(scraper.httpx, "get", lambda url, **kw: _Resp(payload={
        "name": "Analytics Engineer", "job_description": "<p>Model <b>data</b></p>", "location": "Remote"}))
    r = scraper._eightfold("https://explore.jobs.netflix.net/careers/job/790317705130")
    assert r and r.source_type == "eightfold" and "model" in r.raw_text.lower() and "data" in r.raw_text.lower()
    assert scraper._eightfold("https://example.com/about") is None


def test_jsonld_extractor(monkeypatch):
    from resumaker.providers.scrape import scraper
    html_doc = """<html><head>
      <script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage"}</script>
      <script type="application/ld+json">{"@type":"JobPosting","title":"Validation Engineer",
        "description":"<p>Validate <b>systems</b> end to end</p>",
        "hiringOrganization":{"name":"Takeda"},
        "jobLocation":{"address":{"addressLocality":"Thousand Oaks","addressRegion":"CA"}}}</script>
      </head><body>x</body></html>"""
    monkeypatch.setattr(scraper.httpx, "get", lambda url, **kw: _Resp(text=html_doc))
    r = scraper._jsonld("https://jobs.takeda.com/en/job/123")
    assert r and r.source_type == "jsonld" and r.title == "Validation Engineer"
    assert "validate" in r.raw_text.lower() and "systems" in r.raw_text.lower() and r.company == "Takeda"
    assert r.location == "Thousand Oaks, CA"
    # no JobPosting -> None (falls through to Playwright)
    monkeypatch.setattr(scraper.httpx, "get", lambda url, **kw: _Resp(text="<html><body>no jd</body></html>"))
    assert scraper._jsonld("https://example.com/x") is None


def test_dashboard_stats(tmp_db):
    from resumaker.analytics import dashboard_stats
    from resumaker.domain import JobRecord, TrackerEntry
    from resumaker.persistence import db
    for ext, co, src in [("1", "Anthropic", "greenhouse"), ("2", "Anthropic", "greenhouse"),
                         ("3", "OpenAI", "ashby")]:
        db.upsert_job(JobRecord(source=src, external_id=ext, title="ML Engineer",
                                company=co, location="SF, CA", url=f"u{ext}", content_hash=ext))
    db.upsert_tracker(TrackerEntry(url="u1", company="Anthropic", stage="applied"))
    db.upsert_tracker(TrackerEntry(url="u3", company="OpenAI", stage="interested"))
    s = dashboard_stats()
    assert s["watchlist"]["jobs"] == 3 and s["watchlist"]["tracked"] == 2
    assert s["jobs_by_company"]["Anthropic"] == 2
    assert s["jobs_by_source"]["greenhouse"] == 2 and s["jobs_by_source"]["ashby"] == 1
    assert s["tracker_funnel"] == {"applied": 1, "interested": 1}
    assert sum(d["count"] for d in s["new_listings_daily"]) == 3   # all first-seen today


def test_enrichment_proposals(tmp_path, monkeypatch):
    from resumaker.domain import TrackerEntry
    from resumaker.enrichment import proposals as pr
    monkeypatch.setattr(pr, "get_settings",
                        lambda: type("S", (), {"output_root": tmp_path})())
    monkeypatch.setattr(pr.db, "list_tracker",
                        lambda: [TrackerEntry(id=1, url="u", run_id="acme-mle", company="Acme")])
    monkeypatch.setattr(pr.profile, "all_skills", lambda: {"Python"})
    d = tmp_path / "acme-mle"
    d.mkdir()
    (d / "report.json").write_text(json.dumps({"gap": {"items": [
        {"requirement": "Kubernetes orchestration", "status": "supportedByResume",
         "evidence": "ran k8s in prod"},
        {"requirement": "Rust systems programming", "status": "gap"},
        {"requirement": "Python scripting", "status": "supportedByResume"},  # already listed
        {"requirement": "Go", "status": "existing"},                          # not a signal
    ]}}))
    out = pr.propose_from_tracker()
    have = [p.requirement for p in out["have_but_unlisted"]]
    gaps = [p.requirement for p in out["recurring_gaps"]]
    assert "Kubernetes orchestration" in have
    assert "Python scripting" not in have          # a listed skill appears -> skipped
    assert gaps == ["Rust systems programming"]
    assert out["have_but_unlisted"][0].companies == ["Acme"]


def test_discovery_on_target_gate(tmp_db, monkeypatch):
    from resumaker.domain import JobRecord
    from resumaker.ingestion import DiscoveryFilters, discover
    from resumaker.persistence import db
    monkeypatch.setattr("resumaker.enrichment.preferences",
                        lambda: {"target_roles": ["engineer"], "avoid_roles": ["security"]})
    for ext, title in [("1", "ML Engineer"), ("2", "Security Engineer"), ("3", "Recruiter")]:
        db.upsert_job(JobRecord(source="greenhouse", external_id=ext, title=title,
                                company="Acme", location="Boston, MA", content_hash=ext))
    r = discover(DiscoveryFilters(on_target=True))
    assert {j.title for j in r.jobs} == {"ML Engineer"}   # engineer target, security avoided
    assert r.total == 1


@pytest.mark.parametrize("loc,is_us", [
    ("Boston, MA", True), ("New York, NY", True), ("Chicago, IL", True),
    ("Remote - US", True), ("United States", True), ("San Francisco, California", True),
    ("Dublin, CA", True),                         # Dublin, California (US abbr wins)
    ("", True),                                    # unknown -> keep
    ("Austin", True),                              # bare US city (no comma) -> keep
    ("Remote", True),                              # remote -> keep
    ("Bengaluru, India", False), ("Krakow, Poland", False), ("London, United Kingdom", False),
    ("Toronto, ON, Canada", False), ("Gdansk, Poland", False), ("Singapore", False),
    ("Bratislava, Bratislava", False),             # 'City, Region' no US signal -> foreign
    ("Cambridge, England", False),
    ("Bangalore, In", False), ("Hyderabad, In", False),  # 'In' == India, not Indiana
    ("Indianapolis, IN", True),                    # no foreign marker -> Indiana abbr wins
    ("Atlanta, Boston", True), ("Boston, Chicago", True),  # multi US city, no state token
])
def test_is_us_location(loc, is_us):
    assert service.is_us_location(loc) is is_us


@pytest.mark.parametrize("loc,states", [
    ("San Jose, CA", ["CA"]),
    ("San Francisco, CA | New York City, NY", ["CA", "NY"]),
    ("US-CA-Menlo Park", ["CA"]),
    ("Austin, Texas", ["TX"]),
    ("2 Locations", []), ("Remote - USA", []), ("", []),  # unresolved -> OTHER bucket
    ("Bangalore, In", ["IN"]),                     # note: kept only for parity; dropped at ingest
    ("Atlanta, Boston", ["GA", "MA"]),             # multi-city -> both states via city map
])
def test_us_states_of(loc, states):
    assert service.us_states_of(loc) == states


@pytest.mark.parametrize("title,level", [
    ("Machine Learning Intern", "intern"), ("Data Science Co-op", "intern"),
    ("Engineering Manager", "manager"), ("Senior Manager, Data Science", "manager"),
    ("Staff Software Engineer", "staff"), ("Principal AI Engineer", "staff"),
    ("Senior Data Engineer", "senior"), ("Lead ML Engineer", "senior"),
    ("New Grad Software Engineer", "junior"), ("Junior Developer", "junior"),
    ("Data Scientist", "mid"), ("Software Engineer", "mid"),
    ("International Growth Analyst", "mid"),        # 'intern' must not fire on 'international'
])
def test_title_level(title, level):
    assert service.title_level(title) == level


def test_mckinsey_job_url():
    from resumaker.providers.sources.mckinsey import mckinsey_job_url
    # Best-effort fallback slug rule (matches live `friendlyURL` 100/100): lowercase, keep
    # ASCII hyphen-minus, strip every other non-alnum incl. en/em dashes, collapse + trim.
    # en-dash '–' is stripped -> NO hyphen before quantumblack
    assert mckinsey_job_url("Knowledge Graph Data Engineer – QuantumBlack, AI by McKinsey", "110946") == (
        "https://www.mckinsey.com/careers/search-jobs/jobs/"
        "knowledgegraphdataengineerquantumblackaibymckinsey-110946")
    # ASCII hyphen '-' is kept -> hyphen survives before quantumblack
    assert mckinsey_job_url("Senior Knowledge Graph Data Engineer - QuantumBlack, AI by McKinsey", "110947") == (
        "https://www.mckinsey.com/careers/search-jobs/jobs/"
        "seniorknowledgegraphdataengineer-quantumblackaibymckinsey-110947")
    # no title -> bare id form (last resort)
    assert mckinsey_job_url("", "110946") == "https://www.mckinsey.com/careers/search-jobs/jobs/110946"


@pytest.mark.parametrize("title,is_tech", [
    ("Senior Machine Learning Engineer", True), ("Software Engineer II", True),
    ("Data Scientist", True), ("Data Engineer, Platform", True), ("AI Engineer", True),
    ("Site Reliability Engineer", True), ("Backend Developer", True),
    ("Cloud Solutions Architect", True), ("SDET", True),
    ("Sales Engineer", False),                 # non-tech marker overrides "engineer"
    ("Technical Recruiter", False), ("Financial Analyst", False),
    ("Registered Nurse", False), ("Warehouse Associate", False),
    ("Marketing Manager", False), ("Store Manager", False),
    ("Product Designer", False),               # ambiguous -> default drop (no tech marker)
    ("Data Consultant", True), ("Analytics Consultant", True),  # tech-qualified consulting kept
    ("Statistician", True), ("Applied Scientist", True), ("Research Scientist", True),
    ("Financial Analyst", False), ("Management Consultant", False),  # pure-business still dropped
])
def test_is_tech_role(title, is_tech):
    assert service.is_tech_role(title) is is_tech


def test_preference_filter(monkeypatch):
    monkeypatch.setattr("resumaker.enrichment.preferences",
                        lambda: {"target_roles": ["engineer"], "avoid_roles": ["sales"]})
    assert service.matches_preferences("Machine Learning Engineer") is True
    assert service.matches_preferences("Sales Engineer") is False   # avoid wins
    assert service.matches_preferences("Product Manager") is False  # no target kw


def test_notify_digest_and_dedupe(tmp_db):
    from resumaker.domain import JobRecord
    from resumaker.ingestion import notify
    from resumaker.persistence import db
    jobs = [
        JobRecord(source="greenhouse", external_id="1", title="Machine Learning Engineer",
                  company="Acme", location="SF, CA", url="https://x/1", content_hash="1",
                  comp="$200K – $250K"),
        JobRecord(source="ashby", external_id="2", title="Data Engineer", company="Beta",
                  location="NYC", url="https://x/2", content_hash="2"),
        JobRecord(source="greenhouse", external_id="3", title="Office Manager", company="Acme",
                  location="SF", url="https://x/3", content_hash="3"),   # off-target -> excluded
    ]
    # pending = on-target (net match) AND not-yet-emailed
    p = notify.pending(jobs)
    assert {j.external_id for j in p} == {"1", "2"}
    subject, html_body, text_body = notify.build_digest(p)
    assert "2 new" in subject
    assert "Machine Learning Engineer" in html_body and "$200K – $250K" in html_body
    assert "https://x/1" in text_body
    # dry-run counts without needing email config, and does NOT mark
    assert notify.email_new(jobs, dry_run=True) == 2
    assert len(notify.pending(jobs)) == 2
    # once marked, they never re-notify
    db.mark_notified(p)
    assert notify.pending(jobs) == []


def test_matches_preferences_broad_net(monkeypatch):
    # avoid labels carry '(pure)' qualifiers in the real profile - must still block the role
    monkeypatch.setattr("resumaker.enrichment.preferences", lambda: {
        "target_roles": ["AI Engineer", "Machine Learning Engineer", "Data Scientist"],
        "avoid_roles": ["Security Engineer", "Frontend Engineer (pure)",
                        "Site Reliability Engineer (pure infra)"]})
    for t in ["Software Development Engineer, ECS", "Applied Scientist", "ML Engineer",
              "Data Analyst", "Enterprise Systems Architecture", "Senior Data Scientist"]:
        assert service.matches_preferences(t) is True, t
    for t in ["Security Engineer", "Frontend Engineer", "Senior Site Reliability Engineer",
              "Data Center Engineering Operations Technician", "IT Support Engineer",
              "Email Marketing Manager"]:
        assert service.matches_preferences(t) is False, t


def test_google_parse_response():
    from resumaker.providers.sources.google import parse_response
    # ds:1 blob: data = [ [job...], null, total, page_size ]. Job is a positional array.
    job = ["87598862385980102", "Staff Software Engineer", "https://apply", ["r"], ["q"],
           "co/path", None, "Google", "en-US",
           [["Sunnyvale, CA, USA", [], "Sunnyvale", "94089", "CA", "US"]],
           ["desc"], [2, 3], [1782808699, 0], [1782808699, 0], [1782808699, 0]]
    html = ("<script>AF_initDataCallback({key: 'ds:1', hash: '1', data:"
            + json.dumps([[job], None, 1575, 20]) + ", sideChannel: {}});</script>")
    stubs, total = parse_response(html)
    assert total == 1575 and len(stubs) == 1
    assert stubs[0].external_id == "87598862385980102"
    assert stubs[0].title == "Staff Software Engineer"
    assert stubs[0].location == "Sunnyvale, CA, USA"
    assert stubs[0].updated_at.startswith("2026")
    assert parse_response("<html>no ds:1 here</html>") == ([], 0)


def test_tesla_parse_response():
    from resumaker.providers.sources.tesla import parse_response
    body = {"lookup": {"locations": {"401022": "Palo Alto, California",
                                     "500": "Toronto, Ontario"}},
            "listings": [{"id": "224501", "t": "AI Engineer, Optimus", "dp": "3", "l": "401022"},
                         {"id": "9", "t": "Foo", "dp": "1", "l": "500"}]}
    stubs = parse_response(body)
    assert len(stubs) == 2
    assert stubs[0].external_id == "224501" and stubs[0].title == "AI Engineer, Optimus"
    assert stubs[0].location == "Palo Alto, California"
    assert stubs[0].url.endswith("/job/224501")


def test_pcsx_parse_response():
    from resumaker.providers.sources.pcsx import parse_response
    body = {"data": {"count": 2, "positions": [
        {"id": 446717683325, "displayJobId": "3088561", "name": "ML Engineer",
         "standardizedLocations": ["San Diego, CA, US"],
         "locations": ["San Diego, California, USA"], "postedTs": 1774569600,
         "positionUrl": "/careers/job/446717683325"}]}}
    stubs, total = parse_response(body)
    assert total == 2 and len(stubs) == 1
    assert stubs[0].external_id == "446717683325" and stubs[0].title == "ML Engineer"
    assert stubs[0].location == "San Diego, CA, US"       # standardized (US suffix) preferred
    assert stubs[0].updated_at.startswith("2026")
    assert stubs[0].url == "https://app.eightfold.ai/careers/job/446717683325"


def test_meta_handshake_and_parse():
    from resumaker.providers.sources.meta import (
        extract_doc_id,
        find_bundle_urls,
        parse_response,
        scrape_lsd,
    )
    page = ('...["LSD",[],{"token":"AbC_123"}]... '
            '"https://static.xx.fbcdn.net/rsrc.php/v3/yb/abc.js?_nc_x=1" '
            'other "https://z.fbcdn.net/rsrc.php/def.js"')
    assert scrape_lsd(page) == "AbC_123"
    bundles = find_bundle_urls(page)
    assert bundles[0].endswith("abc.js?_nc_x=1") and len(bundles) == 2
    # doc_id lives in a Relay operation module inside a JS bundle, not the page HTML:
    js = ('__d("CareersJobSearchResultsDataQuery_candidate_portalRelayOperation",[],'
          '(function(t,n,r,o,a,i){a.exports="27506805582236862"}),null);')
    assert extract_doc_id(js) == "27506805582236862"
    assert extract_doc_id("no operation module here") == ""
    body = {"data": {"job_search_with_featured_jobs": {
        "all_jobs": [{"id": "111", "title": "SWE",
                      "locations": ["Menlo Park, CA", "Seattle, WA"]}],
        "featured_jobs": [{"id": "222", "title": "MLE", "locations": ["Remote, US"]}]}}}
    stubs = parse_response(body)
    assert {s.external_id for s in stubs} == {"111", "222"}
    assert stubs[0].location == "Menlo Park, CA, Seattle, WA"
    assert stubs[0].url == "https://www.metacareers.com/jobs/111/"


def test_paradox_parse_response():
    from resumaker.providers.sources.paradox import parse_response
    body = {"totalJob": 2363, "jobs": [
        {"uniqueID": "PDX_FEC_ABC", "reference": "P25-354057-1", "title": "Software Engineer",
         "applyURL": "https://fedex.paradox.ai/co/X/Job?job_id=P25-354057-1",
         "locations": [{"city": "Memphis", "stateAbbr": "TN", "countryAbbr": "US"}],
         "customFields": [{"cfKey": "cf_effective_date", "value": "2026-08-07"}]}]}
    stubs, total = parse_response(body)
    assert total == 2363 and len(stubs) == 1
    assert stubs[0].external_id == "PDX_FEC_ABC" and stubs[0].title == "Software Engineer"
    assert stubs[0].location == "Memphis, TN, US" and stubs[0].updated_at == "2026-08-07"


def test_ibm_parse_response():
    from resumaker.providers.sources.ibm import parse_response
    body = {"hits": {"total": {"value": 140, "relation": "eq"}, "hits": [
        {"_id": "sha", "_source": {"id": "sha", "title": "Senior Software Engineer",
         "url": "https://careers.ibm.com/careers/JobDetail?jobId=127642",
         "field_keyword_05": "United States", "field_keyword_19": "Austin, US"}}]}}
    stubs, total = parse_response(body)
    assert total == 140 and len(stubs) == 1
    assert stubs[0].external_id == "127642"            # numeric jobId lifted from the url
    assert stubs[0].title == "Senior Software Engineer" and stubs[0].location == "Austin, US"


def test_icims_parse_page():
    from resumaker.providers.sources.icims import parse_page, total_pages
    row = ('<div class="row"><a class="iCIMS_Anchor" '
           'href="https://careers-suffolkconstruction.icims.com/jobs/9597/general-superintendent/job?x=1" '
           'title="9597 - General Superintendent"><h3>General Superintendent</h3></a>'
           '<span class="glyphicons-map-marker"></span><dd>US-TX-Austin | US-TX-Wilmer</dd></div>')
    html = f'<span>Page 1 of 15</span>{row}'
    assert total_pages(html) == 15
    stubs = parse_page(html)
    assert len(stubs) == 1
    assert stubs[0].external_id == "9597" and stubs[0].title == "General Superintendent"
    assert stubs[0].location == "Austin, TX, US"        # US-TX-Austin normalized, first of pipe list
    assert stubs[0].url.endswith("/general-superintendent/job")


def test_wayfair_parse_response():
    from resumaker.providers.sources.wayfair import parse_response
    body = {"jobListData": [
        {"id": 60428, "eid": "9812", "title": "Software Engineer",
         "location": {"name": "Boston, MA", "city": "Boston", "state": "MA",
                      "country": "US", "countryId": 1},
         "applyLink": "https://wayfair.avature.net/en_US/careers?folderId=9812",
         "lastUpdatedDate": "2026-05-05T12:47:10.933000"}]}
    stubs = parse_response(body)
    assert len(stubs) == 1
    assert stubs[0].external_id == "60428" and stubs[0].title == "Software Engineer"
    assert stubs[0].location == "Boston, MA"
    assert "avature.net" in stubs[0].url and stubs[0].updated_at.startswith("2026")


def test_microsoft_parse_response():
    from resumaker.providers.sources.microsoft import parse_response
    body = {"operationResult": {"result": {"totalJobs": 2, "jobs": [
        {"jobId": "1846000", "title": "Senior Software Engineer",
         "properties": {"locations": ["Redmond, Washington, United States"],
                        "postingDate": "2026-08-08T00:00:00Z"}},
        {"jobId": "1846001", "title": "Data Scientist",
         "properties": {"primaryLocation": "Remote, US", "postingDate": "2026-08-07"}},
    ]}}}
    stubs, total = parse_response(body)
    assert total == 2 and len(stubs) == 2
    assert stubs[0].external_id == "1846000" and "Redmond" in stubs[0].location
    assert stubs[0].title == "Senior Software Engineer" and stubs[0].updated_at.startswith("2026")
    assert stubs[1].location == "Remote, US"


def test_fetch_source_group_concurrency_and_budget(monkeypatch):
    """Per-tenant groups fetch every board (concurrently); a deadline already in the past starts
    no new fetch (the sweep's wall-clock budget), so a throttling host can't overrun the tick."""
    import time

    from resumaker.domain import BoardRef, Company
    from resumaker.ingestion import service

    calls: list[str] = []

    def _fake(company, board):
        calls.append(board.token)
        return (company, board, [], "")

    monkeypatch.setattr(service, "_fetch_board", _fake)
    items = [(Company(name=f"c{i}"), BoardRef(source="workday", token=f"t{i}")) for i in range(6)]

    out = service._fetch_source_group(items, deadline=None, workers=3)   # concurrent per-tenant
    assert len(out) == 6 and sorted(calls) == [f"t{i}" for i in range(6)]

    calls.clear()
    past = time.monotonic() - 1
    assert service._fetch_source_group(items, deadline=past, workers=3) == [] and calls == []
    assert service._fetch_source_group(items, deadline=past, workers=1) == [] and calls == []


def test_ats_run_id_uses_posting_name(tmp_db):
    """The run_id/folder derives from the ATS company+title (what the tracker keeps), not the JD
    body - so a run reads 'morgan-stanley-ai-engineer-<hash>', not 'software-engineering-iii'."""
    from resumaker.domain import TrackerEntry
    from resumaker.ingestion.tracker import _ats_run_id

    rid = _ats_run_id(TrackerEntry(url="https://x/jobs/1", company="Morgan Stanley", title="AI Engineer"))
    assert rid and rid.startswith("morgan-stanley-ai-engineer-")
    assert _ats_run_id(TrackerEntry(url="https://x/jobs/2")) is None   # no company/title -> pipeline derives
