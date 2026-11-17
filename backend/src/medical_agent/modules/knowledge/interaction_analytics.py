"""Administrative interaction analytics derived from persisted conversations."""

import re
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medical_agent.api.dependencies import owner_reference
from medical_agent.modules.conversation.models import Conversation, Message

DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
USER_REFERENCE_PATTERN = re.compile(r"^[0-9a-fA-F]{16}$")


def format_user_reference(value: str) -> str:
    normalized = value.upper()
    return "-".join(normalized[index : index + 4] for index in range(0, 16, 4))


def normalize_user_reference(value: str) -> str:
    normalized = re.sub(r"[\s-]", "", value)
    if not USER_REFERENCE_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="invalid_user_reference")
    return normalized.lower()


def utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def local_day_boundary(value: date, *, next_day: bool = False) -> datetime:
    day = value + timedelta(days=1) if next_day else value
    return datetime.combine(day, time.min, tzinfo=DISPLAY_TIMEZONE).astimezone(UTC)


def _bucket_key(value: datetime, granularity: str) -> tuple[str, str]:
    local = utc_datetime(value).astimezone(DISPLAY_TIMEZONE)
    if granularity == "hour":
        key = local.strftime("%Y-%m-%dT%H:00")
        return key, local.strftime("%m-%d %H时")
    if granularity == "week":
        week_start = (local - timedelta(days=local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        key = week_start.strftime("%Y-%m-%d")
        week_end = week_start + timedelta(days=6)
        if week_start.year == week_end.year:
            return key, f"{week_start:%m-%d}–{week_end:%m-%d}"
        return key, f"{week_start:%Y-%m-%d}–{week_end:%Y-%m-%d}"
    if granularity == "month":
        key = local.strftime("%Y-%m")
        return key, local.strftime("%Y-%m")
    if granularity == "quarter":
        quarter = (local.month - 1) // 3 + 1
        return f"{local.year}-Q{quarter}", f"{local.year} Q{quarter}"
    if granularity.startswith("year"):
        span = int(granularity.partition(":")[2] or "1")
        first_year = local.year - local.year % span
        label = str(first_year) if span == 1 else f"{first_year}–{first_year + span - 1}"
        return str(first_year), label
    key = local.strftime("%Y-%m-%d")
    return key, local.strftime("%m-%d")


def _granularity(start_at: datetime | None, end_at: datetime | None) -> str:
    if not start_at or not end_at:
        return "day"
    for candidate in ("day", "week", "month", "quarter", "year"):
        if len(_trend_buckets(start_at, end_at, candidate)) <= 15:
            return candidate
    span = 2
    while len(_trend_buckets(start_at, end_at, f"year:{span}")) > 15:
        span += 1
    return f"year:{span}"


def _bucket_start(value: datetime, granularity: str) -> datetime:
    local = utc_datetime(value).astimezone(DISPLAY_TIMEZONE)
    if granularity == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    if granularity == "week":
        local -= timedelta(days=local.weekday())
    if granularity == "month":
        return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if granularity == "quarter":
        first_month = (local.month - 1) // 3 * 3 + 1
        return local.replace(
            month=first_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    if granularity.startswith("year"):
        span = int(granularity.partition(":")[2] or "1")
        first_year = local.year - local.year % span
        return local.replace(
            year=first_year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_bucket(value: datetime, granularity: str) -> datetime:
    if granularity == "hour":
        return value + timedelta(hours=1)
    if granularity == "day":
        return value + timedelta(days=1)
    if granularity == "week":
        return value + timedelta(days=7)
    if granularity == "quarter":
        month = value.month + 3
        return value.replace(
            year=value.year + (month - 1) // 12,
            month=(month - 1) % 12 + 1,
        )
    if granularity.startswith("year"):
        span = int(granularity.partition(":")[2] or "1")
        return value.replace(year=value.year + span)
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _trend_buckets(
    start_at: datetime, end_at: datetime, granularity: str
) -> list[tuple[str, str]]:
    if end_at <= start_at:
        return []
    cursor = _bucket_start(start_at, granularity)
    local_end = utc_datetime(end_at).astimezone(DISPLAY_TIMEZONE)
    buckets: list[tuple[str, str]] = []
    while cursor < local_end:
        buckets.append(_bucket_key(cursor, granularity))
        cursor = _next_bucket(cursor, granularity)
    return buckets


async def analytics(
    db: AsyncSession,
    *,
    start_date: date | None,
    end_date: date | None,
    user_id: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="invalid_date_range")

    start_at = local_day_boundary(start_date) if start_date else None
    end_at = local_day_boundary(end_date, next_day=True) if end_date else None
    normalized_user = normalize_user_reference(user_id) if user_id.strip() else ""

    owner_rows = await db.scalars(
        select(Conversation.owner_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .where(
            Message.role == "user",
            ~Conversation.owner_id.like("admin-test-%"),
        )
        .distinct()
    )
    all_owners = list(owner_rows)
    owner_refs = {owner_id: owner_reference(owner_id) for owner_id in all_owners}
    matched_owners = (
        [owner_id for owner_id, reference in owner_refs.items() if reference == normalized_user]
        if normalized_user
        else all_owners
    )

    conditions = [Message.role == "user", ~Conversation.owner_id.like("admin-test-%")]
    if start_at is not None:
        conditions.append(Message.created_at >= start_at)
    if end_at is not None:
        conditions.append(Message.created_at < end_at)
    if normalized_user:
        if not matched_owners:
            return {
                "summary": {
                    "total_users": len(all_owners),
                    "matched_users": 0,
                    "matched_interactions": 0,
                    "average_interactions": 0.0,
                },
                "trend": [],
                "top_users": [],
                "items": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
            }
        conditions.append(Conversation.owner_id.in_(matched_owners))

    interaction_rows = (
        await db.execute(
            select(Message.id, Message.created_at, Conversation.owner_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
            .order_by(Message.created_at.asc())
        )
    ).all()
    matched_interactions = len(interaction_rows)
    matched_owner_ids = {row.owner_id for row in interaction_rows}
    matched_users = len(matched_owner_ids)

    effective_start = start_at or (
        utc_datetime(interaction_rows[0].created_at) if interaction_rows else None
    )
    effective_end = end_at or datetime.now(UTC)
    granularity = _granularity(effective_start, effective_end)
    trend_counts: Counter[str] = Counter()
    trend_users: dict[str, set[str]] = defaultdict(set)
    trend_labels: dict[str, str] = {}
    owner_counts: Counter[str] = Counter()
    for row in interaction_rows:
        key, label = _bucket_key(row.created_at, granularity)
        trend_counts[key] += 1
        trend_users[key].add(row.owner_id)
        trend_labels[key] = label
        owner_counts[row.owner_id] += 1

    bucket_order: list[str] = []
    if effective_start is not None:
        for key, label in _trend_buckets(effective_start, effective_end, granularity):
            bucket_order.append(key)
            trend_labels.setdefault(key, label)
    for key in sorted(trend_counts):
        if key not in trend_labels:
            trend_labels[key] = key
        if key not in bucket_order:
            bucket_order.append(key)

    trend = [
        {
            "key": key,
            "label": trend_labels[key],
            "interactions": trend_counts[key],
            "users": len(trend_users[key]),
        }
        for key in bucket_order
    ]
    top_users = [
        {
            "user_id": format_user_reference(owner_refs[owner_id]),
            "interactions": count,
        }
        for owner_id, count in owner_counts.most_common(8)
    ]

    page_rows = (
        await db.execute(
            select(Message, Conversation.owner_id, Conversation.title)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    conversation_ids = {row.Message.conversation_id for row in page_rows}
    assistant_rows = []
    if conversation_ids:
        assistant_rows = list(
            await db.scalars(
                select(Message)
                .where(
                    Message.conversation_id.in_(conversation_ids),
                    Message.role == "assistant",
                )
                .order_by(Message.conversation_id, Message.sequence_no)
            )
        )
    assistants_by_conversation: dict[str, list[Message]] = defaultdict(list)
    assistant_sequences: dict[str, list[int]] = defaultdict(list)
    for assistant in assistant_rows:
        assistants_by_conversation[assistant.conversation_id].append(assistant)
        assistant_sequences[assistant.conversation_id].append(assistant.sequence_no)

    items: list[dict[str, Any]] = []
    for row in page_rows:
        user_message = row.Message
        sequences = assistant_sequences[user_message.conversation_id]
        index = bisect_right(sequences, user_message.sequence_no)
        assistants = assistants_by_conversation[user_message.conversation_id]
        assistant = assistants[index] if index < len(assistants) else None
        items.append(
            {
                "id": user_message.id,
                "user_id": format_user_reference(owner_refs[row.owner_id]),
                "conversation_id": user_message.conversation_id,
                "conversation_title": row.title,
                "input": user_message.content,
                "output": assistant.content if assistant else "",
                "status": assistant.status if assistant else "pending",
                "created_at": utc_datetime(user_message.created_at)
                .astimezone(DISPLAY_TIMEZONE)
                .isoformat(),
            }
        )

    return {
        "summary": {
            "total_users": len(all_owners),
            "matched_users": matched_users,
            "matched_interactions": matched_interactions,
            "average_interactions": round(
                matched_interactions / matched_users if matched_users else 0.0, 2
            ),
        },
        "trend": trend,
        "top_users": top_users,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": matched_interactions,
    }


async def export_items(
    db: AsyncSession,
    *,
    start_date: date | None,
    end_date: date | None,
    user_id: str,
) -> list[dict[str, Any]]:
    result = await analytics(
        db,
        start_date=start_date,
        end_date=end_date,
        user_id=user_id,
        page=1,
        page_size=1_000_000,
    )
    return [
        {
            **item,
            "created_at": datetime.fromisoformat(item["created_at"]),
        }
        for item in reversed(result["items"])
        if item["output"].strip()
    ]
