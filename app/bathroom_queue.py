"""Persistent, user-started browser work; this module never requests source sites."""
from __future__ import annotations

import asyncio
import math
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal
from app.krisha_search import normalize_krisha_import_url, normalize_krisha_listing_url
from app.models import BathroomCheckControl, BathroomCheckJob, BathroomCheckTask, Listing, SearchResult
from app.search_analysis import is_target_search_result, parse_bathrooms_count

async def _allow_control_origin(request: Request) -> None:
    if request.method != "POST":
        return
    origin = request.headers.get("origin")
    if not origin and request.headers.get("sec-fetch-site") != "cross-site":
        return  # local CLI and the extension background service may omit Origin
    parsed = urlsplit(origin or "")
    expected = urlsplit(str(request.base_url))
    if parsed.scheme == "chrome-extension" and parsed.netloc and not parsed.path:
        return
    if parsed.scheme in {"http", "https"} and (parsed.scheme, parsed.netloc) == (expected.scheme, expected.netloc):
        return
    raise HTTPException(403, "Очередью можно управлять из ApartmentAgent или его расширения.")


router = APIRouter(prefix="/api/v1/search/bathroom-check", tags=["bathroom-check"],
                   dependencies=[Depends(_allow_control_origin)])
_queue_lock = asyncio.Lock()
LEASE_SECONDS = 120
GAP_SECONDS = 15
DETAILS_EXTENSION_RELEASE = "0.4.1"
# Changing extraction rules can justify one fresh pass over incomplete cards.
PRIORITY_DETAILS_REVISION = 2
_finished = {"confirmed", "rejected", "unknown", "failed", "expired"}


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _criteria(mode: str = "bathrooms") -> dict:
    settings = get_settings()
    result = {"target_rooms": settings.target_rooms, "max_price_kzt": settings.max_price_kzt,
            "min_area_m2": settings.min_area_m2, "max_area_m2": settings.max_area_m2,
            "min_bathrooms": settings.min_bathrooms, "exclude_pledged": settings.exclude_pledged, "mode": mode}
    if mode == "details":
        result["priority_details_revision"] = PRIORITY_DETAILS_REVISION
    return result


def _missing_priority_details(row) -> tuple[str, ...]:
    return tuple(name for name, present in (
        ("repair", bool((row.repair_condition or "").strip())),
        ("mortgage", row.mortgage_supported is not None),
        ("author", bool((row.seller_name or "").strip() or (row.seller_type or "").strip())),
    ) if not present)


_priority_columns = (Listing.repair_condition, Listing.mortgage_supported, Listing.seller_name, Listing.seller_type)


async def _control(session: AsyncSession) -> BathroomCheckControl:
    # A permanent lock row serializes even two processes starting a fresh job.
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    await session.execute(insert(BathroomCheckControl).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return await session.scalar(select(BathroomCheckControl).where(BathroomCheckControl.id == 1).with_for_update())


async def _job(session: AsyncSession, control: BathroomCheckControl) -> BathroomCheckJob | None:
    if control.current_job_id is None:
        return None
    return await session.scalar(select(BathroomCheckJob).where(BathroomCheckJob.id == control.current_job_id).with_for_update())


async def _tasks(session: AsyncSession, job: BathroomCheckJob) -> list[BathroomCheckTask]:
    return list((await session.scalars(select(BathroomCheckTask).where(BathroomCheckTask.job_id == job.id).order_by(BathroomCheckTask.id))).all())


async def _candidates(session: AsyncSession, criteria: dict) -> list[tuple[str, int | None]]:
    rows = list((await session.scalars(select(SearchResult).order_by(SearchResult.id))).all())
    # Any known count for a canonical URL, including a legacy www/query variant,
    # prevents that same apartment from being queued as unknown.
    known = {}
    pledged = set()
    for row in rows:
        url = normalize_krisha_listing_url(row.url)
        if row.is_pledged is True:
            pledged.add(url)
        count = row.bathrooms_count
        if count is None:
            count = parse_bathrooms_count(f"{row.title or ''} {row.snippet or ''}")
        if count is not None:
            known[url] = count
    listing_values = (await session.execute(select(Listing.source_url, Listing.bathrooms_count,
        Listing.details_imported_at, Listing.rooms, Listing.area_m2, Listing.price_kzt,
        Listing.is_pledged, *_priority_columns))).all()
    saved = {}
    for value in listing_values:
        url = normalize_krisha_listing_url(value.source_url)
        if url not in saved or (
            value.details_imported_at is not None
            and (saved[url].details_imported_at is None
                 or _aware(value.details_imported_at) > _aware(saved[url].details_imported_at))
        ):
            saved[url] = value
        if value.bathrooms_count is not None:
            known[url] = value.bathrooms_count
    for url, value in saved.items():
        if value.is_pledged is True:
            pledged.add(url)
        elif value.is_pledged is False:
            pledged.discard(url)
    result, seen = [], set()
    details_mode = criteria.get("mode") == "details"
    revision = criteria.get("priority_details_revision")
    reviewed = set()
    if details_mode and revision:
        reviewed = {normalize_krisha_listing_url(url) for url in await session.scalars(
            select(BathroomCheckTask.url)
            .join(BathroomCheckJob, BathroomCheckJob.id == BathroomCheckTask.job_id)
            .where(BathroomCheckJob.criteria["priority_details_revision"].as_integer() == revision,
                   BathroomCheckTask.last_outcome.in_(("imported", "unavailable")))
        )}

    def eligible(url):
        if not url or (criteria.get("exclude_pledged") and url in pledged):
            return False
        if details_mode:
            if url in reviewed or (criteria["min_bathrooms"] == 2 and url in known and known[url] < 2):
                return False
            row = saved.get(url)
            return row is None or row.details_imported_at is None or bool(revision and _missing_priority_details(row))
        return url not in known

    def matches(title, snippet):
        return is_target_search_result(title or "", snippet, target_rooms=criteria["target_rooms"],
                                       max_price=criteria["max_price_kzt"],
                                       min_area=criteria["min_area_m2"], max_area=criteria["max_area_m2"])

    def saved_title(value):
        return f"{value.rooms}-комнатная квартира, {value.area_m2:g} м², {value.price_kzt} ₸"

    for row in rows:
        url = normalize_krisha_listing_url(row.url)
        if not eligible(url) or url in seen:
            continue
        title, snippet = (saved_title(saved[url]), None) if details_mode and url in saved else (row.title, row.snippet)
        if not matches(title, snippet):
            continue
        seen.add(url)
        result.append((url, row.id))
    if details_mode:
        for url, value in saved.items():
            if eligible(url) and url not in seen and matches(saved_title(value), None):
                seen.add(url)
                result.append((url, None))
    return result


async def _expire(session: AsyncSession, job: BathroomCheckJob | None, now: datetime) -> None:
    if job is None or job.status not in {"running", "paused"}:
        return
    for task in await _tasks(session, job):
        if task.status == "leased" and task.lease_expires_at and _aware(task.lease_expires_at) <= now:
            task.status, task.completed_at = "expired", now
            task.error = "Браузер не завершил объявление за 120 секунд. Автоматический повтор отключён."
            job.status = "paused"
            job.message = task.error + " Нажмите «Продолжить» для оставшихся объявлений."
            job.next_claim_at = now + timedelta(seconds=GAP_SECONDS)


async def _status(session: AsyncSession, job: BathroomCheckJob | None) -> dict:
    criteria = job.criteria if job else _criteria()
    mode = criteria.get("mode", "bathrooms")
    candidates = len(await _candidates(session, _criteria("bathrooms")))
    details_candidates = len(await _candidates(session, _criteria("details")))
    tasks = await _tasks(session, job) if job else []
    counts = {key: sum(task.status == key for task in tasks) for key in _finished}
    leased = next((task for task in tasks if task.status == "leased"), None)
    saved = {}
    for row in (await session.execute(select(Listing.source_url, Listing.details_imported_at, *_priority_columns)
                                      .where(Listing.details_imported_at.is_not(None)))).all():
        url = normalize_krisha_listing_url(row.source_url)
        if url not in saved or _aware(row.details_imported_at) > _aware(saved[url].details_imported_at):
            saved[url] = row
    snapshots = [_missing_priority_details(saved[task.url]) for task in tasks
                 if task.status in {"confirmed", "rejected", "unknown"}
                 and task.last_outcome != "unavailable" and task.url in saved
                 and (task.claimed_at is None or _aware(saved[task.url].details_imported_at) >= _aware(task.claimed_at))]
    detail_fields = {"complete": sum(not missing for missing in snapshots),
                     **{f"missing_{name}": sum(name in missing for missing in snapshots)
                        for name in ("repair", "mortgage", "author")}}
    return {"job_id": job.id if job else None, "status": job.status if job else "idle",
            "total": len(tasks), "pending": sum(task.status in {"pending", "leased"} for task in tasks),
            "checked": sum(counts.values()), "confirmed": counts["confirmed"],
            "rejected": counts["rejected"], "unknown": counts["unknown"],
            "failed": counts["failed"] + counts["expired"], "current_url": leased.url if leased else None,
            "message": job.message if job else None, "criteria": criteria, "candidate_count": candidates,
            "mode": mode, "details_candidate_count": details_candidates, "enriched": len(snapshots),
            "detail_fields": detail_fields,
            "unavailable": sum(task.last_outcome == "unavailable" for task in tasks)}


@router.get("")
@router.get("/status", include_in_schema=False)
async def bathroom_check_status() -> dict:
    async with _queue_lock, SessionLocal() as session, session.begin():
        control = await _control(session)
        job = await _job(session, control)
        await _expire(session, job, _now())
        await session.flush()
        return await _status(session, job)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["bathrooms", "details"] = "bathrooms"
    source_url: str | None = Field(default=None, max_length=2000)

    @field_validator("source_url")
    @classmethod
    def normalize_source(cls, value):
        if value is None:
            return None
        canonical = normalize_krisha_import_url(value)
        if canonical is None:
            raise ValueError("Укажите номер объявления или ссылку Krisha")
        return canonical

    @model_validator(mode="after")
    def single_import_mode(self):
        if self.source_url and self.mode != "details":
            raise ValueError("Импорт по номеру выполняется в режиме дополнения карточек")
        return self


@router.post("/start")
async def bathroom_check_start(request: StartRequest | None = None) -> dict:
    async with _queue_lock, SessionLocal() as session, session.begin():
        control = await _control(session)
        job = await _job(session, control)
        await _expire(session, job, _now())
        if request and request.source_url and job is not None and job.status in {"running", "paused"}:
            raise HTTPException(409, "Другая очередь уже запущена. Завершите или остановите её перед импортом по номеру.")
        if job is None or job.status not in {"running", "paused"}:
            criteria = _criteria(request.mode if request else "bathrooms")
            if request and request.source_url:
                criteria.update(manual_import=True, source_url=request.source_url)
                candidates = [(request.source_url, None)]
            else:
                candidates = await _candidates(session, criteria)
            job = BathroomCheckJob(status="running" if candidates else "completed", criteria=criteria,
                                   message=None if candidates else ("Нет объявлений для получения подробностей." if criteria["mode"] == "details" else "Нет объявлений с неизвестным количеством санузлов."))
            session.add(job)
            await session.flush()
            control.current_job_id = job.id
            session.add_all(BathroomCheckTask(job_id=job.id, search_result_id=result_id, url=url, status="pending")
                            for url, result_id in candidates)
        await session.flush()
        return await _status(session, job)


async def _change(action: str) -> dict:
    async with _queue_lock, SessionLocal() as session, session.begin():
        control = await _control(session)
        job = await _job(session, control)
        await _expire(session, job, _now())
        if job and job.status in {"running", "paused"}:
            if action == "pause":
                job.status, job.message = "paused", "Проверка приостановлена пользователем."
            elif action == "resume":
                tasks = await _tasks(session, job)
                retry = next((task for task in reversed(tasks)
                              if task.status == "expired" or (task.status == "failed" and task.last_outcome == "blocked")), None)
                if retry is not None:
                    count = await _stored_count(session, retry.url)
                    details_ready = await _details_ready(session, retry.url, retry.claimed_at) if job.criteria.get("mode") == "details" else True
                    if details_ready and (count is not None or job.criteria.get("mode") == "details"):
                        retry.status = "unknown" if count is None else "confirmed" if count >= 2 else "rejected"
                        retry.error = None
                    elif retry.attempts < 2:
                        # Only an explicit user resume permits one retry; a
                        # browser poll or restarted service cannot do this.
                        retry.status = "pending"
                        retry.worker_id = retry.lease_token = retry.lease_expires_at = None
                        retry.completed_at = retry.error = None
                job.status = "running" if any(task.status in {"pending", "leased"} for task in tasks) else "completed"
                job.message = None
            elif action == "cancel":
                job.status, job.message = "cancelled", "Проверка остановлена пользователем."
                for task in await _tasks(session, job):
                    if task.status == "leased":
                        task.status = "pending"
                        task.worker_id = task.lease_token = task.lease_expires_at = None
        await session.flush()
        return await _status(session, job)


@router.post("/pause")
async def bathroom_check_pause() -> dict:
    return await _change("pause")


@router.post("/resume")
async def bathroom_check_resume() -> dict:
    return await _change("resume")


@router.post("/cancel")
async def bathroom_check_cancel() -> dict:
    return await _change("cancel")


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=128)
    extension_version: str | None = Field(default=None, max_length=30)


class CompleteRequest(ClaimRequest):
    task_id: int = Field(gt=0)
    lease_token: str = Field(min_length=1, max_length=128)
    outcome: Literal["imported", "unknown", "unavailable", "blocked", "error"]
    error: str | None = Field(default=None, max_length=1000)


@router.post("/claim")
async def bathroom_check_claim(request: ClaimRequest) -> dict:
    async with _queue_lock, SessionLocal() as session, session.begin():
        control = await _control(session)
        job = await _job(session, control)
        now = _now()
        await _expire(session, job, now)
        result = {"job_id": job.id if job else None, "status": job.status if job else "idle",
                  "task": None, "wait_seconds": GAP_SECONDS, "message": job.message if job else None,
                  "mode": job.criteria.get("mode", "bathrooms") if job else "bathrooms"}
        if job is None or job.status != "running":
            return result
        if job.criteria.get("mode") == "details":
            version = request.extension_version or ""
            parts = tuple(int(part) for part in version.split(".")) if re.fullmatch(r"\d+(?:\.\d+){1,3}", version) else ()
            revision = job.criteria.get("priority_details_revision")
            if revision == 1:
                release = "0.4.0"
            elif revision:
                release = DETAILS_EXTENSION_RELEASE
            else:
                release = "0.3.0" if job.criteria.get("manual_import") else "0.2.0"
            minimum = tuple(map(int, release.split("."))) + (0,)
            if not parts or (parts + (0,) * (4 - len(parts))) < minimum:
                job.status = "paused"
                job.message = f"Для дополнения карточек нужно обновить расширение до {release}."
                result.update(status=job.status, message=job.message)
                return result
        tasks = await _tasks(session, job)
        task = next((task for task in tasks if task.status == "leased"), None)
        if task and task.worker_id != request.worker_id:
            result["message"] = "Текущее объявление уже проверяет другой браузер."
            return result
        if task is None:
            if job.next_claim_at and _aware(job.next_claim_at) > now:
                result["wait_seconds"] = max(GAP_SECONDS, math.ceil((_aware(job.next_claim_at) - now).total_seconds()))
                return result
            task = None
            for candidate in tasks:
                if candidate.status != "pending":
                    continue
                count = await _stored_count(session, candidate.url)
                details_mode = job.criteria.get("mode") == "details"
                if details_mode:
                    # A prior partial save is exactly why this URL was selected.
                    revision = job.criteria.get("priority_details_revision")
                    fresh_since = candidate.claimed_at or (job.created_at if revision else None)
                    ready = await _details_ready(session, candidate.url, fresh_since,
                                                 require_priority_fields=bool(revision))
                else:
                    ready = count is not None
                if job.criteria.get("manual_import"):
                    ready = False  # An explicit number import refreshes an existing card too.
                if ready:
                    candidate.status = "unknown" if count is None else "confirmed" if count >= 2 else "rejected"
                    candidate.completed_at = now
                    continue
                task = candidate
                break
            if task is None:
                job.status, job.message = "completed", "Проверка завершена."
                result.update(status="completed", message=job.message)
                return result
            task.status, task.worker_id = "leased", request.worker_id
            task.attempts += 1
            task.lease_token = secrets.token_urlsafe(24)
            task.claimed_at, task.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
        result["task"] = {"id": task.id, "url": task.url, "lease_token": task.lease_token, "mode": result["mode"]}
        return result


async def _stored_count(session: AsyncSession, url: str) -> int | None:
    canonical = normalize_krisha_listing_url(url)
    path = canonical.removeprefix("https://krisha.kz")
    # Prefer the saved listing. A client's claimed count is never accepted.
    listings = (await session.execute(select(Listing.source_url, Listing.bathrooms_count)
                                     .where(Listing.source_url.contains(path, autoescape=True)))).all()
    for saved_url, count in listings:
        if normalize_krisha_listing_url(saved_url) == canonical and count is not None:
            return count
    rows = (await session.scalars(select(SearchResult).where(SearchResult.url.contains(path, autoescape=True)))).all()
    for row in rows:
        if normalize_krisha_listing_url(row.url) == canonical and row.bathrooms_count is not None:
            return row.bathrooms_count
    return None


async def _details_ready(session: AsyncSession, url: str, since: datetime | None = None,
                         *, require_priority_fields: bool = False) -> bool:
    canonical = normalize_krisha_listing_url(url)
    rows = (await session.execute(select(Listing.source_url, Listing.details_imported_at, *_priority_columns)
            .where(Listing.source_url.contains(canonical.removeprefix("https://krisha.kz"), autoescape=True)))).all()
    return any(normalize_krisha_listing_url(row.source_url) == canonical and row.details_imported_at is not None
               and (since is None or _aware(row.details_imported_at) >= _aware(since))
               and (not require_priority_fields or not _missing_priority_details(row)) for row in rows)


@router.post("/complete")
async def bathroom_check_complete(request: CompleteRequest) -> dict:
    async with _queue_lock, SessionLocal() as session, session.begin():
        control = await _control(session)
        job = await _job(session, control)
        now = _now()
        await _expire(session, job, now)
        task = await session.get(BathroomCheckTask, request.task_id)
        if (job is None or task is None or task.job_id != job.id or job.status == "cancelled"
                or task.worker_id != request.worker_id or task.lease_token != request.lease_token):
            raise HTTPException(409, "Задание уже остановлено или принадлежит другому браузеру.")
        if task.status in _finished:
            return await _status(session, job)
        if task.status != "leased":
            raise HTTPException(409, "Время проверки истекло. Автоматический повтор отключён.")
        if request.outcome in {"blocked", "error"}:
            task.status, task.error = "failed", request.error or "Источник запросил проверку или браузер не смог прочитать объявление."
            job.status, job.message = "paused", "Проверка приостановлена: " + task.error
        elif request.outcome == "unavailable":
            task.status, task.error = "unknown", request.error or "Объявление снято с публикации."
        else:
            count = await _stored_count(session, task.url)
            if job.criteria.get("mode") == "details" and not await _details_ready(session, task.url, task.claimed_at):
                count = None
                job.status = "paused"
                job.message = "Подробные данные объявления не получены. Проверьте расширение и нажмите «Продолжить»."
                if request.error:
                    job.message += " Причина: " + request.error[:500]
            task.status = "unknown" if count is None else "confirmed" if count >= 2 else "rejected"
            task.error = request.error if task.status == "unknown" else None
        task.completed_at = now
        task.last_outcome = ("unknown" if request.outcome == "imported" and job.status == "paused" else request.outcome)
        job.next_claim_at = now + timedelta(seconds=GAP_SECONDS)
        await session.flush()
        tasks = await _tasks(session, job)
        if job.status == "running" and not any(task.status in {"pending", "leased"} for task in tasks):
            job.status, job.message = "completed", "Проверка завершена."
        return await _status(session, job)
