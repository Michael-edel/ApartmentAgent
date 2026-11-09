import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import bathroom_queue as queue
from app.models import Base, BathroomCheckJob, BathroomCheckTask, Listing, SearchResult


@pytest_asyncio.fixture
async def env(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = SimpleNamespace(target_rooms=4, max_price_kzt=50_000_000,
                               min_area_m2=80, max_area_m2=120, min_bathrooms=0, exclude_pledged=True)
    clock = {"now": datetime(2026, 11, 6, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr(queue, "SessionLocal", sessions)
    monkeypatch.setattr(queue, "get_settings", lambda: settings)
    monkeypatch.setattr(queue, "_now", lambda: clock["now"])
    monkeypatch.setattr(queue, "_queue_lock", asyncio.Lock())
    yield SimpleNamespace(sessions=sessions, settings=settings, clock=clock)
    await engine.dispose()


async def seed(env, urls=("https://krisha.kz/a/show/100",), *, count=None, title=None):
    async with env.sessions() as session:
        session.add_all(SearchResult(url=url, title=title or "4-комнатная квартира, 96 м², 38 млн тг",
                                    query="test", search_engine="test", bathrooms_count=count)
                        for url in urls)
        await session.commit()


async def store_count(env, url, count):
    async with env.sessions() as session:
        session.add(Listing(source="browser", source_url=url, title="Тестовая квартира",
                            price_kzt=38_000_000, area_m2=96, rooms=4, bathrooms_count=count))
        await session.commit()


async def claim(worker="browser-1"):
    return await queue.bathroom_check_claim(queue.ClaimRequest(worker_id=worker))


async def complete(result, outcome="imported", worker="browser-1", **kwargs):
    task = result["task"]
    return await queue.bathroom_check_complete(queue.CompleteRequest(
        worker_id=worker, task_id=task["id"], lease_token=task["lease_token"], outcome=outcome, **kwargs))


@pytest.mark.asyncio
async def test_start_deduplicates_unknown_even_without_filter_and_preserves_settings(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://www.krisha.kz/a/show/100?from=search"))
    await seed(env, ("https://krisha.kz/a/show/200",), count=1)
    await seed(env, ("https://krisha.kz/a/show/300",), count=2)
    await seed(env, ("https://krisha.kz/a/show/400",), title="3-комнатная квартира, 96 м², 38 млн тг")
    await seed(env, ("https://krisha.kz/a/show/500",), title="4-комнатная квартира, 96 м², 60 млн тг")
    await seed(env, ("https://krisha.kz/a/show/600",))
    await store_count(env, "https://www.krisha.kz/a/show/600?x=1", 1)
    before = await queue.bathroom_check_status()
    assert before["status"] == "idle" and before["candidate_count"] == 1
    started = await queue.bathroom_check_start()
    assert started["total"] == started["pending"] == 1
    assert started["criteria"]["min_bathrooms"] == env.settings.min_bathrooms == 0
    async with env.sessions() as session:
        assert len((await session.scalars(select(SearchResult))).all()) == 7
        task = await session.scalar(select(BathroomCheckTask))
        assert task.url == "https://krisha.kz/a/show/100"
    env.settings.target_rooms = 3
    assert (await queue.bathroom_check_status())["criteria"]["target_rooms"] == 4


@pytest.mark.asyncio
async def test_concurrent_start_and_claim_have_one_job_and_one_lease(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200"))
    starts = await asyncio.gather(queue.bathroom_check_start(), queue.bathroom_check_start())
    assert starts[0]["job_id"] == starts[1]["job_id"]
    results = await asyncio.gather(claim("worker-a"), claim("worker-b"))
    winners = [result for result in results if result["task"]]
    assert len(winners) == 1
    same = await claim("worker-a" if results[0]["task"] else "worker-b")
    assert same["task"] == winners[0]["task"]
    async with env.sessions() as session:
        assert len((await session.scalars(select(BathroomCheckJob))).all()) == 1
        tasks = (await session.scalars(select(BathroomCheckTask))).all()
        assert sum(task.status == "leased" for task in tasks) == 1
        assert sum(task.attempts for task in tasks) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count,expected", [(2, "confirmed"), (1, "rejected"), (None, "unknown")])
async def test_completion_uses_saved_database_count_and_is_idempotent(env, count, expected):
    await seed(env)
    await queue.bathroom_check_start()
    leased = await claim()
    if count is not None:
        await store_count(env, "https://www.krisha.kz/a/show/100?browser=1", count)
    # Even a client saying "unknown" cannot override a known saved count.
    result = await complete(leased, "unknown")
    assert result[expected] == result["checked"] == 1
    assert result["pending"] == 0 and result["status"] == "completed"
    assert await complete(leased, "error", error="duplicate") == result
    assert (await claim())["task"] is None


@pytest.mark.asyncio
async def test_unknown_completion_keeps_diagnostic_without_becoming_save_failure(env):
    await seed(env)
    await queue.bathroom_check_start()
    leased = await claim()
    result = await complete(leased, "unknown", error="Не удалось прочитать: price_kzt")
    assert result["unknown"] == 1 and result["failed"] == 0
    async with env.sessions() as session:
        task = await session.get(BathroomCheckTask, leased["task"]["id"])
        assert task.error == "Не удалось прочитать: price_kzt"


@pytest.mark.asyncio
async def test_claim_spacing_and_snapshot_do_not_depend_on_changed_search_settings(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200"))
    await queue.bathroom_check_start()
    first = await claim()
    await complete(first)
    env.settings.target_rooms = 2
    assert (await claim())["task"] is None
    env.clock["now"] += timedelta(seconds=14)
    assert (await claim())["task"] is None
    env.clock["now"] += timedelta(seconds=1)
    second = await claim()
    assert second["task"] and second["task"]["id"] != first["task"]["id"]


@pytest.mark.asyncio
async def test_pause_blocks_claims_cancel_invalidates_lease_and_start_creates_new_job(env):
    await seed(env)
    started = await queue.bathroom_check_start()
    leased = await claim()
    paused = await queue.bathroom_check_pause()
    assert paused["status"] == "paused" and (await claim())["task"] is None
    assert (await queue.bathroom_check_start())["job_id"] == started["job_id"]
    assert (await queue.bathroom_check_resume())["status"] == "running"
    assert (await claim())["task"] == leased["task"]
    cancelled = await queue.bathroom_check_cancel()
    assert cancelled["status"] == "cancelled" and cancelled["current_url"] is None
    assert (await claim())["task"] is None
    with pytest.raises(HTTPException) as invalid:
        await complete(leased)
    assert invalid.value.status_code == 409
    assert (await queue.bathroom_check_start())["job_id"] != started["job_id"]


@pytest.mark.asyncio
async def test_expired_lease_pauses_without_automatic_retry_and_manual_resume_retries_once(env):
    await seed(env)
    await queue.bathroom_check_start()
    first = await claim()
    env.clock["now"] += timedelta(seconds=120)
    status = await queue.bathroom_check_status()
    assert status["status"] == "paused" and status["failed"] == 1
    assert (await claim())["task"] is None
    await queue.bathroom_check_resume()
    assert (await claim())["task"] is None  # the safety gap still applies
    env.clock["now"] += timedelta(seconds=15)
    second = await claim()
    assert second["task"]["id"] == first["task"]["id"]
    assert second["task"]["lease_token"] != first["task"]["lease_token"]
    with pytest.raises(HTTPException):
        await complete(first)
    env.clock["now"] += timedelta(seconds=120)
    await queue.bathroom_check_status()
    assert (await queue.bathroom_check_resume())["status"] == "completed"
    assert (await claim())["task"] is None


@pytest.mark.asyncio
async def test_blocked_pauses_and_resume_resolves_saved_page_without_reopening(env):
    await seed(env)
    await queue.bathroom_check_start()
    leased = await claim()
    blocked = await complete(leased, "blocked", error="Пройдите проверку браузера")
    assert blocked["status"] == "paused" and blocked["failed"] == 1
    assert (await claim())["task"] is None
    await store_count(env, "https://krisha.kz/a/show/100", 2)
    resumed = await queue.bathroom_check_resume()
    assert resumed["status"] == "completed" and resumed["confirmed"] == 1 and resumed["failed"] == 0
    assert (await claim())["task"] is None


@pytest.mark.asyncio
async def test_error_is_not_retried_but_remaining_tasks_can_resume(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200"))
    await queue.bathroom_check_start()
    first = await claim()
    assert (await complete(first, "error", error="Страница недоступна"))["status"] == "paused"
    await queue.bathroom_check_resume()
    env.clock["now"] += timedelta(seconds=15)
    second = await claim()
    assert second["task"]["id"] != first["task"]["id"]


@pytest.mark.asyncio
async def test_manual_pause_allows_current_import_and_resume_continues_next(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200"))
    await queue.bathroom_check_start()
    first = await claim()
    await queue.bathroom_check_pause()
    await store_count(env, "https://krisha.kz/a/show/100", 2)
    result = await complete(first)
    assert result["status"] == "paused" and result["confirmed"] == 1
    assert result["current_url"] is None and result["pending"] == 1
    assert (await claim())["task"] is None
    assert (await queue.bathroom_check_resume())["status"] == "running"
    env.clock["now"] += timedelta(seconds=15)
    assert (await claim())["task"]["id"] != first["task"]["id"]


@pytest.mark.asyncio
async def test_only_explicit_resume_of_paused_job_advances_persisted_generation(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200"))
    started = await queue.bathroom_check_start()
    assert started["criteria"].get("resume_generation", 0) == 0
    leased = await claim()
    blocked = await complete(leased, "blocked", error="Проверка браузера")
    assert blocked["status"] == "paused"
    assert (await queue.bathroom_check_status())["criteria"].get("resume_generation", 0) == 0
    resumed = await queue.bathroom_check_resume()
    assert resumed["status"] == "running" and resumed["job_id"] == started["job_id"]
    assert resumed["criteria"]["resume_generation"] == 1
    assert (await queue.bathroom_check_resume())["criteria"]["resume_generation"] == 1
    assert (await queue.bathroom_check_pause())["criteria"]["resume_generation"] == 1
    assert (await queue.bathroom_check_resume())["criteria"]["resume_generation"] == 2
    assert (await queue.bathroom_check_status())["criteria"]["resume_generation"] == 2


@pytest.mark.asyncio
async def test_pending_task_saved_elsewhere_is_resolved_without_browser_claim(env):
    await seed(env)
    await queue.bathroom_check_start()
    await store_count(env, "https://krisha.kz/a/show/100", 2)
    result = await claim()
    assert result["status"] == "completed" and result["task"] is None
    status = await queue.bathroom_check_status()
    assert status["confirmed"] == 1 and status["checked"] == 1


@pytest.mark.asyncio
async def test_ownership_and_http_contract_reject_untrusted_count(env):
    from app.main import app

    await seed(env)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://queue.test") as client:
        assert (await client.get("/api/v1/search/bathroom-check")).json()["status"] == "idle"
        assert (await client.get("/api/v1/search/bathroom-check/status")).status_code == 200
        assert (await client.post("/api/v1/search/bathroom-check/start", headers={"Origin": "https://krisha.kz"})).status_code == 403
        assert (await client.post("/api/v1/search/bathroom-check/start", headers={"Origin": "http://queue.test"})).status_code == 200
        assert (await client.post("/api/v1/search/bathroom-check/start", headers={"Origin": "chrome-extension://test"})).status_code == 200
        assert (await client.post("/api/v1/search/bathroom-check/start")).json()["total"] == 1
        leased = (await client.post("/api/v1/search/bathroom-check/claim", json={"worker_id": "browser-1"})).json()
        payload = {"worker_id": "browser-1", "task_id": leased["task"]["id"],
                   "lease_token": leased["task"]["lease_token"], "outcome": "imported"}
        assert (await client.post("/api/v1/search/bathroom-check/complete", json={**payload, "bathrooms_count": 2})).status_code == 422
        assert (await client.post("/api/v1/search/bathroom-check/complete", json={**payload, "worker_id": "thief"})).status_code == 409
        assert (await client.post("/api/v1/search/bathroom-check/complete", json=payload)).json()["unknown"] == 1


@pytest.mark.asyncio
async def test_details_mode_includes_saved_only_and_known_two_but_respects_minimum(env):
    await seed(env)
    await seed(env, ("https://krisha.kz/a/show/200",), count=1)
    await seed(env, ("https://krisha.kz/a/show/300",), count=2)
    await store_count(env, "https://krisha.kz/a/show/400", 2)
    await store_count(env, "https://krisha.kz/a/show/500", 2)
    async with env.sessions() as session:
        row = await session.scalar(select(Listing).where(Listing.source_url.endswith("500")))
        row.details_imported_at = env.clock["now"]
        row.repair_condition, row.mortgage_supported, row.seller_type = "Хороший ремонт", False, "Собственник"
        await session.commit()
    env.settings.min_bathrooms = 2
    before = await queue.bathroom_check_status()
    assert before["details_candidate_count"] == 3
    result = await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    assert result["mode"] == result["criteria"]["mode"] == "details"
    assert result["total"] == 3
    async with env.sessions() as session:
        tasks = (await session.scalars(select(BathroomCheckTask))).all()
        assert {task.url for task in tasks} == {f"https://krisha.kz/a/show/{i}" for i in (100, 300, 400)}
        assert next(task for task in tasks if task.url.endswith("400")).search_result_id is None
    await queue.bathroom_check_cancel()
    env.settings.min_bathrooms = 0
    assert (await queue.bathroom_check_start(queue.StartRequest(mode="details")))["total"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "0.1.10", "0.3.1", "0.4.0", "invalid"])
async def test_details_requires_new_extension_before_any_task_is_leased(env, version):
    await seed(env)
    await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    result = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=version))
    assert result["status"] == "paused" and result["task"] is None and queue.DETAILS_EXTENSION_RELEASE in result["message"]
    async with env.sessions() as session:
        assert (await session.scalar(select(BathroomCheckTask))).attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [queue.DETAILS_EXTENSION_RELEASE, f"{queue.DETAILS_EXTENSION_RELEASE}.1"])
async def test_details_known_count_still_requires_new_full_import(env, version):
    await seed(env, count=2)
    await store_count(env, "https://krisha.kz/a/show/100", 2)
    await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    leased = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=version))
    assert leased["mode"] == leased["task"]["mode"] == "details"
    result = await complete(leased)
    assert result["confirmed"] == 0 and result["unknown"] == 1 and result["enriched"] == 0
    assert result["status"] == "paused" and "не получены" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("count,expected", [(2, "confirmed"), (1, "rejected"), (None, "unknown")])
async def test_details_completion_reports_enrichment_independently_of_bathroom_count(env, count, expected):
    await seed(env)
    await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    leased = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE))
    await store_count(env, "https://krisha.kz/a/show/100", count)
    async with env.sessions() as session:
        row = await session.scalar(select(Listing))
        row.details_imported_at = env.clock["now"] + timedelta(seconds=1)
        row.property_details = {"Балкон": "Есть"}
        await session.commit()
    result = await complete(leased)
    assert result["status"] == "completed" and result["enriched"] == 1 and result[expected] == 1
    assert result["detail_fields"] == {"complete": 0, "missing_repair": 1, "missing_mortgage": 1, "missing_author": 1}
    assert result["details_candidate_count"] == 0
    assert result["candidate_count"] == (1 if count is None else 0)


@pytest.mark.asyncio
async def test_details_timestamp_older_than_claim_cannot_confirm_new_task(env):
    await seed(env, count=2)
    await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    leased = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE))
    await store_count(env, "https://krisha.kz/a/show/100", 2)
    async with env.sessions() as session:
        row = await session.scalar(select(Listing))
        row.details_imported_at = env.clock["now"] - timedelta(minutes=1)
        await session.commit()
    result = await complete(leased)
    assert result["status"] == "paused" and result["enriched"] == result["confirmed"] == 0


@pytest.mark.asyncio
async def test_details_pause_explains_unreadable_fields(env):
    await seed(env)
    await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    leased = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE))
    result = await complete(leased, "unknown", error="Не удалось прочитать: price_kzt")
    assert result["status"] == "paused" and result["enriched"] == 0
    assert "Причина: Не удалось прочитать: price_kzt" in result["message"]


@pytest.mark.asyncio
async def test_details_mode_does_not_repeat_enriched_legacy_url_variant(env):
    await seed(env)
    await store_count(env, "https://www.krisha.kz/a/show/100?legacy=1", 2)
    async with env.sessions() as session:
        row = await session.scalar(select(Listing))
        row.details_imported_at = env.clock["now"]
        row.repair_condition, row.mortgage_supported, row.seller_type = "Хороший ремонт", False, "Собственник"
        await session.commit()
    await store_count(env, "https://krisha.kz/a/show/100", None)
    result = await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    assert result["status"] == "completed" and result["total"] == 0


@pytest.mark.asyncio
async def test_single_number_import_does_not_require_discovery_or_match_search_filters(env):
    started = await queue.bathroom_check_start(queue.StartRequest(mode="details", source_url="1010095181"))
    assert started["total"] == 1 and started["criteria"]["manual_import"] is True
    old = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version="0.2.2"))
    assert old["task"] is None and old["status"] == "paused" and queue.DETAILS_EXTENSION_RELEASE in old["message"]
    await queue.bathroom_check_resume()
    ready = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE))
    assert ready["task"]["url"] == "https://krisha.kz/a/show/1010095181"
    async with env.sessions() as session:
        assert (await session.scalar(select(BathroomCheckTask))).search_result_id is None


@pytest.mark.asyncio
async def test_single_number_import_refreshes_existing_full_card(env):
    await store_count(env, "https://krisha.kz/a/show/123", 2)
    async with env.sessions() as session:
        row = await session.scalar(select(Listing))
        row.details_imported_at = env.clock["now"] - timedelta(hours=1)
        await session.commit()
    await queue.bathroom_check_start(queue.StartRequest(mode="details", source_url="123"))
    result = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE))
    assert result["task"] is not None


@pytest.mark.asyncio
async def test_single_number_import_cannot_replace_active_batch(env):
    await seed(env)
    started = await queue.bathroom_check_start()
    with pytest.raises(HTTPException) as busy:
        await queue.bathroom_check_start(queue.StartRequest(mode="details", source_url="123"))
    assert busy.value.status_code == 409
    assert (await queue.bathroom_check_status())["job_id"] == started["job_id"]


@pytest.mark.asyncio
async def test_single_number_http_contract_rejects_wrong_mode_and_foreign_urls(env):
    from app.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://queue.test") as client:
        for payload in ({"source_url":"123"}, {"mode":"details", "source_url":"https://example.invalid/123"}):
            assert (await client.post("/api/v1/search/bathroom-check/start", json=payload)).status_code == 422
    assert (await queue.bathroom_check_status())["job_id"] is None


@pytest.mark.asyncio
async def test_partial_saved_card_is_leased_once_per_extractor_revision(env, monkeypatch):
    await seed(env, count=2)
    await store_count(env, "https://www.krisha.kz/a/show/100?old=1", 2)
    async with env.sessions() as session:
        row = await session.scalar(select(Listing))
        row.details_imported_at = env.clock["now"] - timedelta(days=1)
        row.repair_condition = "С ремонтом"
        await session.commit()
    started = await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    assert started["total"] == 1
    leased = await queue.bathroom_check_claim(queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE))
    assert leased["task"]["url"] == "https://krisha.kz/a/show/100"
    async with env.sessions() as session:
        row = await session.scalar(select(Listing))
        row.details_imported_at = env.clock["now"] + timedelta(seconds=1)
        row.mortgage_supported = False
        await session.commit()
    completed = await complete(leased)
    assert completed["status"] == "completed" and completed["details_candidate_count"] == 0
    assert completed["detail_fields"] == {"complete": 0, "missing_repair": 0, "missing_mortgage": 0, "missing_author": 1}
    assert (await queue.bathroom_check_start(queue.StartRequest(mode="details")))["total"] == 0
    monkeypatch.setattr(queue, "PRIORITY_DETAILS_REVISION", queue.PRIORITY_DETAILS_REVISION + 1)
    assert (await queue.bathroom_check_start(queue.StartRequest(mode="details")))["total"] == 1


@pytest.mark.asyncio
async def test_archived_detail_does_not_pause_or_repeat(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200"))
    await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    worker = queue.ClaimRequest(worker_id="browser-1", extension_version=queue.DETAILS_EXTENSION_RELEASE)
    first = await queue.bathroom_check_claim(worker)
    result = await complete(first, "unavailable")
    assert result["status"] == "running" and result["pending"] == 1
    assert result["unavailable"] == 1 and result["enriched"] == result["failed"] == 0
    env.clock["now"] += timedelta(seconds=queue.GAP_SECONDS)
    second = await queue.bathroom_check_claim(worker)
    assert second["task"]["url"] == "https://krisha.kz/a/show/200"
    await complete(second, "unavailable")
    assert (await queue.bathroom_check_start(queue.StartRequest(mode="details")))["total"] == 0


@pytest.mark.asyncio
async def test_batch_queue_excludes_known_pledge_but_not_unknown(env):
    await seed(env, ("https://krisha.kz/a/show/100", "https://krisha.kz/a/show/200", "https://krisha.kz/a/show/300"))
    await store_count(env, "https://www.krisha.kz/a/show/100?from=browser", None)
    async with env.sessions() as session:
        saved = await session.scalar(select(Listing))
        saved.is_pledged = True
        explicit_no = await session.scalar(select(SearchResult).where(SearchResult.url.endswith("200")))
        explicit_no.is_pledged = False
        await session.commit()
    started = await queue.bathroom_check_start(queue.StartRequest(mode="details"))
    assert started["total"] == started["candidate_count"] == 2
    await queue.bathroom_check_cancel()
    env.settings.exclude_pledged = False
    assert (await queue.bathroom_check_start(queue.StartRequest(mode="details")))["total"] == 3
