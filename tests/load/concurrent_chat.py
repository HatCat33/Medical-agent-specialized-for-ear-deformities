"""Concurrent SSE chat load test for the patient conversation API."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx

DEFAULT_QUESTION = "压力测试：请用简短语言说明儿童耳部日常护理的一般注意事项。"
IMAGE_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
AUDIO_MEDIA_TYPES = {
    ".amr": "audio/amr",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
}


@dataclass(frozen=True, slots=True)
class Fixture:
    name: str
    media_type: str
    data: bytes


@dataclass(slots=True)
class PreparedUser:
    index: int
    visitor_id: str
    conversation_id: str


@dataclass(slots=True)
class RequestResult:
    user: int
    round: int
    success: bool
    status_code: int | None
    first_token_ms: float | None
    total_ms: float
    output_chars: int
    input_type: str = "text"
    conversation_create_ms: float | None = None
    history_read_ms: float | None = None
    input_stage_ms: float | None = None
    answer_done_ms: float | None = None
    first_audio_ms: float | None = None
    tts_total_ms: float | None = None
    tts_requested: bool = False
    tts_segments: int = 0
    audio_bytes: int = 0
    conversation_delete_ms: float | None = None
    error: str | None = None
    assistant_status: str | None = None
    safety_code: str | None = None


@dataclass(slots=True)
class ActiveCounter:
    current: int = 0
    maximum: int = 0

    def enter(self) -> None:
        self.current += 1
        self.maximum = max(self.maximum, self.current)

    def leave(self) -> None:
        self.current -= 1


@dataclass(slots=True)
class TtsMetrics:
    first_audio_ms: float | None = None
    finished_ms: float | None = None
    audio_bytes: int = 0
    segments: int = 0


def percentage_hit(index: int, percent: int, *, salt: int = 0) -> bool:
    """Choose a stable subset without introducing run-to-run randomness."""
    return ((index * 37 + salt) % 100) < percent


def mixed_input_type(index: int, *, text_percent: int, voice_percent: int) -> str:
    bucket = (index * 37) % 100
    if bucket < text_percent:
        return "text"
    if bucket < text_percent + voice_percent:
        return "voice"
    return "image"


def drain_speech_segments(
    buffer: str,
    *,
    emitted_characters: int,
    final: bool,
) -> tuple[list[str], str]:
    """Approximate the patient H5 streaming TTS segmentation policy."""
    segments: list[str] = []
    while buffer:
        first = emitted_characters == 0
        minimum = 12 if first else 28
        maximum = 40 if first else 112
        hard = next(
            (
                match.start() + 1
                for match in re.finditer(r"[。！？!?；;\n]", buffer)
                if 2 <= match.start() + 1 <= maximum
            ),
            None,
        )
        soft = next(
            (
                match.start() + 1
                for match in re.finditer(r"[，,：:、]", buffer)
                if minimum <= match.start() + 1 <= maximum
            ),
            None,
        )
        natural = min(value for value in (hard, soft) if value is not None) if any(
            value is not None for value in (hard, soft)
        ) else None
        length = natural or (maximum if len(buffer) >= maximum else len(buffer) if final else 0)
        if not length:
            break
        segment = buffer[:length].strip()
        buffer = buffer[length:]
        if segment:
            segments.append(segment)
            emitted_characters += len(segment)
    return segments, buffer


async def consume_tts_queue(
    client: httpx.AsyncClient,
    api_base: str,
    visitor_id: str,
    queue: asyncio.Queue[str | None],
    *,
    started: float,
    metrics: TtsMetrics | None = None,
) -> TtsMetrics:
    # Owned by run_request as well, so an incomplete stream retains its samples.
    metrics = metrics if metrics is not None else TtsMetrics()

    async def worker() -> None:
        while True:
            text = await queue.get()
            if text is None:
                # One sentinel fans out to all three patient-like prefetch workers.
                queue.put_nowait(None)
                return
            metrics.segments += 1
            async with client.stream(
                "POST",
                f"{api_base}/speech/syntheses/stream",
                headers={"X-Visitor-ID": visitor_id},
                json={"text": text},
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode(errors="replace")[:300]
                    raise RuntimeError(f"tts_http_{response.status_code}:{detail}")
                segment_bytes = 0
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    first_audio_ms = (time.perf_counter() - started) * 1000
                    if (
                        metrics.first_audio_ms is None
                        or first_audio_ms < metrics.first_audio_ms
                    ):
                        metrics.first_audio_ms = first_audio_ms
                    segment_bytes += len(chunk)
                    metrics.audio_bytes += len(chunk)
                if not segment_bytes:
                    raise RuntimeError("tts_completed_without_audio")

    # Match apps/web-h5/src/lib/pcmStreamPlayback.ts MAX_FETCHING.
    async with asyncio.TaskGroup() as group:
        for _ in range(3):
            group.create_task(worker())
    metrics.finished_ms = (time.perf_counter() - started) * 1000
    return metrics


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def metric_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "samples": len(values),
        "min_ms": round(min(values), 2) if values else None,
        "avg_ms": round(statistics.fmean(values), 2) if values else None,
        "p50_ms": round(percentile(values, 0.50) or 0, 2) if values else None,
        "p95_ms": round(percentile(values, 0.95) or 0, 2) if values else None,
        "p99_ms": round(percentile(values, 0.99) or 0, 2) if values else None,
        "max_ms": round(max(values), 2) if values else None,
    }


def concise_error(exc: BaseException) -> str:
    """Expose the actionable leaf from TaskGroup failures in load-test reports."""
    if isinstance(exc, BaseExceptionGroup):
        leaves = [concise_error(item) for item in exc.exceptions]
        return "exception_group:" + " | ".join(dict.fromkeys(leaves))
    return f"{type(exc).__name__}:{exc}"


def is_local_url(value: str) -> bool:
    hostname = (urlparse(value).hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("base_url_required")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url_must_include_http_or_https")
    return normalized


async def prepare_user(
    client: httpx.AsyncClient,
    api_base: str,
    run_id: str,
    index: int,
) -> PreparedUser:
    visitor_id = f"loadtest-{run_id}-{index:03d}"
    response = await client.post(
        f"{api_base}/conversations",
        headers={"X-Visitor-ID": visitor_id},
        json={},
    )
    response.raise_for_status()
    payload = response.json()
    conversation_id = payload.get("id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise RuntimeError("conversation_id_missing")
    return PreparedUser(index=index, visitor_id=visitor_id, conversation_id=conversation_id)


def parse_sse_block(block: str) -> tuple[str | None, dict[str, object] | None]:
    event: str | None = None
    data_lines: list[str] = []
    for line in block.replace("\r\n", "\n").split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not event or not data_lines:
        return event, None
    raw_data = cast(object, json.loads("\n".join(data_lines)))
    if not isinstance(raw_data, dict):
        raise TypeError("sse_data_not_object")
    return event, cast(dict[str, object], raw_data)


async def run_request(
    client: httpx.AsyncClient,
    api_base: str,
    user: PreparedUser,
    round_number: int,
    question: str,
    start_signal: asyncio.Event,
    timeout_seconds: float,
    active: ActiveCounter,
    scenario: str,
    fixture: Fixture | None,
    tts_streaming: bool,
    speak_response: bool = False,
) -> RequestResult:
    await start_signal.wait()
    started = time.perf_counter()
    first_token_ms: float | None = None
    output_chars = 0
    status_code: int | None = None
    completed = False
    assistant_status: str | None = None
    safety_code: str | None = None
    input_stage_ms: float | None = None
    first_audio_ms: float | None = None
    answer_done_ms: float | None = None
    tts_total_ms: float | None = None
    tts_segments = 0
    audio_bytes = 0
    tts_buffer = ""
    tts_emitted_characters = 0
    tts_queue: asyncio.Queue[str | None] | None = None
    tts_task: asyncio.Task[TtsMetrics] | None = None
    tts_metrics = TtsMetrics()
    if speak_response and scenario != "tts":
        tts_queue = asyncio.Queue()
        tts_task = asyncio.create_task(
            consume_tts_queue(
                client,
                api_base,
                user.visitor_id,
                tts_queue,
                started=started,
                metrics=tts_metrics,
            )
        )
    active.enter()
    try:
        async with asyncio.timeout(timeout_seconds):
            if scenario == "tts":
                endpoint = "syntheses/stream" if tts_streaming else "syntheses"
                async with client.stream(
                    "POST",
                    f"{api_base}/speech/{endpoint}",
                    headers={"X-Visitor-ID": user.visitor_id},
                    json={"text": f"{question}。测试编号 {user.index}"},
                ) as response:
                    status_code = response.status_code
                    if response.status_code != 200:
                        detail = (await response.aread()).decode(errors="replace")[:300]
                        raise RuntimeError(f"tts_http_{response.status_code}:{detail}")
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        if first_audio_ms is None:
                            first_audio_ms = (time.perf_counter() - started) * 1000
                        audio_bytes += len(chunk)
                if not audio_bytes:
                    raise RuntimeError("tts_completed_without_audio")
                return RequestResult(
                    user=user.index,
                    round=round_number,
                    success=True,
                    status_code=status_code,
                    first_token_ms=None,
                    total_ms=(time.perf_counter() - started) * 1000,
                    output_chars=0,
                    first_audio_ms=first_audio_ms,
                    audio_bytes=audio_bytes,
                    tts_requested=True,
                )

            content = f"{question}（并发用户 {user.index}，第 {round_number} 轮）"
            attachment_ids: list[str] = []
            input_mode = "text"
            if scenario == "image":
                if fixture is None:
                    raise RuntimeError("image_fixture_required")
                stage_started = time.perf_counter()
                upload = await client.post(
                    f"{api_base}/media/images",
                    headers={"X-Visitor-ID": user.visitor_id},
                    files=[
                        (
                            "images",
                            (fixture.name, fixture.data, fixture.media_type),
                        )
                    ],
                )
                if upload.status_code != 201:
                    detail = upload.text[:300]
                    raise RuntimeError(f"image_upload_http_{upload.status_code}:{detail}")
                media = upload.json()
                attachment_ids = [str(item["id"]) for item in media]
                input_mode = "multimodal"
                input_stage_ms = (time.perf_counter() - stage_started) * 1000
            elif scenario == "voice":
                if fixture is None:
                    raise RuntimeError("audio_fixture_required")
                stage_started = time.perf_counter()
                transcription = await client.post(
                    f"{api_base}/speech/transcriptions",
                    headers={"X-Visitor-ID": user.visitor_id},
                    files={"audio": (fixture.name, fixture.data, fixture.media_type)},
                )
                if transcription.status_code != 200:
                    detail = transcription.text[:300]
                    raise RuntimeError(
                        f"transcription_http_{transcription.status_code}:{detail}"
                    )
                transcribed = transcription.json().get("text")
                if not isinstance(transcribed, str) or not transcribed.strip():
                    raise RuntimeError("transcription_text_missing")
                content = transcribed.strip()
                input_mode = "voice"
                input_stage_ms = (time.perf_counter() - stage_started) * 1000

            async with client.stream(
                "POST",
                f"{api_base}/conversations/{user.conversation_id}/messages/stream",
                headers={"X-Visitor-ID": user.visitor_id},
                json={
                    "content": content,
                    "attachment_ids": attachment_ids,
                    "input_mode": input_mode,
                    "client_request_id": str(uuid.uuid4()),
                },
            ) as response:
                status_code = response.status_code
                if response.status_code != 200:
                    detail = (await response.aread()).decode(errors="replace")[:300]
                    raise RuntimeError(f"http_{response.status_code}:{detail}")

                block_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line:
                        block_lines.append(line)
                        continue
                    if not block_lines:
                        continue
                    event, data = parse_sse_block("\n".join(block_lines))
                    block_lines = []
                    content_delta = data.get("content") if data else None
                    if event == "delta" and isinstance(content_delta, str):
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - started) * 1000
                        output_chars += len(content_delta)
                        if tts_queue is not None:
                            tts_buffer += content_delta
                            segments, tts_buffer = drain_speech_segments(
                                tts_buffer,
                                emitted_characters=tts_emitted_characters,
                                final=False,
                            )
                            for segment in segments:
                                tts_emitted_characters += len(segment)
                                tts_queue.put_nowait(segment)
                    elif event == "done":
                        completed = True
                        message = data.get("message") if data else None
                        if isinstance(message, dict):
                            message_data = cast(dict[str, object], message)
                            assistant_status = str(message_data.get("status") or "") or None
                            safety_code = str(message_data.get("safety_code") or "") or None
                        break
                    elif event == "error":
                        message = data.get("message") if data else "unknown"
                        raise RuntimeError(f"sse_error:{message}")

                if block_lines:
                    event, data = parse_sse_block("\n".join(block_lines))
                    if event == "done":
                        completed = True
                        message = data.get("message") if data else None
                        if isinstance(message, dict):
                            message_data = cast(dict[str, object], message)
                            assistant_status = str(message_data.get("status") or "") or None
                            safety_code = str(message_data.get("safety_code") or "") or None
                    elif event == "error":
                        message = data.get("message") if data else "unknown"
                        raise RuntimeError(f"sse_error:{message}")
                if not completed:
                    raise RuntimeError("sse_completed_without_done_event")
                if first_token_ms is None:
                    raise RuntimeError("sse_completed_without_delta_event")
                if assistant_status not in {None, "completed"}:
                    raise RuntimeError(f"assistant_status:{assistant_status}")
                if safety_code == "provider_unavailable":
                    raise RuntimeError("assistant_provider_unavailable")
                answer_done_ms = (time.perf_counter() - started) * 1000
                if tts_queue is not None and tts_task is not None:
                    segments, tts_buffer = drain_speech_segments(
                        tts_buffer,
                        emitted_characters=tts_emitted_characters,
                        final=True,
                    )
                    for segment in segments:
                        tts_emitted_characters += len(segment)
                        tts_queue.put_nowait(segment)
                    tts_queue.put_nowait(None)
                    tts_metrics = await tts_task
                    first_audio_ms = tts_metrics.first_audio_ms
                    tts_total_ms = tts_metrics.finished_ms
                    tts_segments = tts_metrics.segments
                    audio_bytes = tts_metrics.audio_bytes
    except TimeoutError:
        if tts_task is not None:
            first_audio_ms = tts_metrics.first_audio_ms
            tts_segments = tts_metrics.segments
            audio_bytes = tts_metrics.audio_bytes
        return RequestResult(
            user=user.index,
            round=round_number,
            success=False,
            status_code=status_code,
            first_token_ms=first_token_ms,
            total_ms=(time.perf_counter() - started) * 1000,
            output_chars=output_chars,
            input_type=scenario,
            input_stage_ms=input_stage_ms,
            answer_done_ms=answer_done_ms,
            first_audio_ms=first_audio_ms,
            tts_total_ms=tts_total_ms,
            tts_requested=speak_response,
            tts_segments=tts_segments,
            audio_bytes=audio_bytes,
            error=f"timeout_after_{timeout_seconds:g}s",
            assistant_status=assistant_status,
            safety_code=safety_code,
        )
    except Exception as exc:  # noqa: BLE001 - load tests must aggregate every failure.
        if tts_task is not None:
            first_audio_ms = tts_metrics.first_audio_ms
            tts_segments = tts_metrics.segments
            audio_bytes = tts_metrics.audio_bytes
        return RequestResult(
            user=user.index,
            round=round_number,
            success=False,
            status_code=status_code,
            first_token_ms=first_token_ms,
            total_ms=(time.perf_counter() - started) * 1000,
            output_chars=output_chars,
            input_type=scenario,
            input_stage_ms=input_stage_ms,
            answer_done_ms=answer_done_ms,
            first_audio_ms=first_audio_ms,
            tts_total_ms=tts_total_ms,
            tts_requested=speak_response,
            tts_segments=tts_segments,
            audio_bytes=audio_bytes,
            error=concise_error(exc),
            assistant_status=assistant_status,
            safety_code=safety_code,
        )
    finally:
        if tts_task is not None and not tts_task.done():
            tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
        active.leave()

    return RequestResult(
        user=user.index,
        round=round_number,
        success=True,
        status_code=status_code,
        first_token_ms=first_token_ms,
        total_ms=(time.perf_counter() - started) * 1000,
        output_chars=output_chars,
        input_type=scenario,
        input_stage_ms=input_stage_ms,
        answer_done_ms=answer_done_ms,
        first_audio_ms=first_audio_ms,
        tts_total_ms=tts_total_ms,
        tts_requested=speak_response,
        tts_segments=tts_segments,
        audio_bytes=audio_bytes,
        assistant_status=assistant_status,
        safety_code=safety_code,
    )


async def cleanup_user(
    client: httpx.AsyncClient,
    api_base: str,
    user: PreparedUser,
) -> str | None:
    if not user.conversation_id:
        return None
    try:
        response = await client.delete(
            f"{api_base}/conversations/{user.conversation_id}",
            headers={"X-Visitor-ID": user.visitor_id},
        )
        if response.status_code != 204:
            return f"user_{user.index}:http_{response.status_code}"
    except Exception as exc:  # noqa: BLE001 - report cleanup failures without hiding results.
        return f"user_{user.index}:{type(exc).__name__}:{exc}"
    return None


def print_metric(label: str, values: dict[str, float | None]) -> None:
    if values["avg_ms"] is None:
        print(f"{label}: 无已记录样本")
        return
    print(
        f"{label}: avg={values['avg_ms']:.2f}ms "
        f"p50={values['p50_ms']:.2f}ms p95={values['p95_ms']:.2f}ms "
        f"p99={values['p99_ms']:.2f}ms max={values['max_ms']:.2f}ms"
    )


async def execute(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.base_url)
    if not is_local_url(base_url) and not args.allow_remote:
        print("拒绝压测远程地址。确认目标后增加 --allow-remote。", file=sys.stderr)
        return 2

    api_base = f"{base_url}/api/v1"
    run_id = uuid.uuid4().hex[:10]
    limits = httpx.Limits(
        max_connections=max(args.users + 20, 70),
        max_keepalive_connections=max(args.users, 50),
    )
    timeout = httpx.Timeout(connect=15, read=None, write=30, pool=30)
    verify = not args.insecure
    prepared: list[PreparedUser] = []
    cleanup_errors: list[str] = []
    fixture: Fixture | None = None
    if args.scenario == "image":
        fixture = load_fixture(args.image_file, IMAGE_MEDIA_TYPES, "--image-file")
    elif args.scenario == "voice":
        fixture = load_fixture(args.audio_file, AUDIO_MEDIA_TYPES, "--audio-file")

    print(f"目标: {base_url}")
    print(f"场景: {args.scenario}")
    print(f"并发患者: {args.users}，轮次: {args.rounds}，总请求: {args.users * args.rounds}")
    if args.scenario == "tts":
        print("注意：请求会调用当前配置的真实 TTS，并可能产生供应商费用。")
    else:
        print("注意：请求会调用当前配置的真实模型/RAG，并可能产生供应商费用。")

    async with httpx.AsyncClient(limits=limits, timeout=timeout, verify=verify) as client:
        try:
            health = await client.get(f"{api_base}/health/live")
            health.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - show a concise preflight failure.
            print(f"健康检查失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        print("正在准备独立访客与会话……")
        if args.scenario == "tts":
            prepared_results: list[PreparedUser | BaseException] = [
                PreparedUser(index, f"loadtest-{run_id}-{index:03d}", "")
                for index in range(1, args.users + 1)
            ]
        else:
            prepared_results = list(
                await asyncio.gather(
                    *(
                        prepare_user(client, api_base, run_id, index)
                        for index in range(1, args.users + 1)
                    ),
                    return_exceptions=True,
                )
            )
        preparation_errors = [item for item in prepared_results if isinstance(item, BaseException)]
        prepared = [item for item in prepared_results if isinstance(item, PreparedUser)]
        if preparation_errors:
            for error in preparation_errors[:10]:
                print(f"会话准备失败: {type(error).__name__}: {error}", file=sys.stderr)
            print(f"仅准备成功 {len(prepared)}/{args.users} 个会话，停止压测。", file=sys.stderr)
            if not args.keep_data:
                await asyncio.gather(*(cleanup_user(client, api_base, user) for user in prepared))
            return 2

        all_results: list[RequestResult] = []
        active = ActiveCounter()
        wall_started = time.perf_counter()
        try:
            for round_number in range(1, args.rounds + 1):
                signal = asyncio.Event()
                tasks = [
                    asyncio.create_task(
                        run_request(
                            client,
                            api_base,
                            user,
                            round_number,
                            args.question,
                            signal,
                            args.timeout,
                            active,
                            args.scenario,
                            fixture,
                            args.tts_streaming,
                        )
                    )
                    for user in prepared
                ]
                print(f"第 {round_number} 轮准备完成，释放 {len(tasks)} 个并发请求。")
                signal.set()
                all_results.extend(await asyncio.gather(*tasks))
        finally:
            wall_seconds = time.perf_counter() - wall_started
            if not args.keep_data:
                removed = await asyncio.gather(
                    *(cleanup_user(client, api_base, user) for user in prepared)
                )
                cleanup_errors = [item for item in removed if item]

    successful = [item for item in all_results if item.success]
    failed = [item for item in all_results if not item.success]
    first_token_values = [
        item.first_token_ms for item in all_results if item.first_token_ms is not None
    ]
    total_values = [item.total_ms for item in successful]
    input_stage_values = [
        item.input_stage_ms for item in all_results if item.input_stage_ms is not None
    ]
    first_audio_values = [
        item.first_audio_ms for item in all_results if item.first_audio_ms is not None
    ]
    success_rate = len(successful) / len(all_results) if all_results else 0
    summary: dict[str, Any] = {
        "run_id": run_id,
        "target": base_url,
        "scenario": args.scenario,
        "users": args.users,
        "rounds": args.rounds,
        "total_requests": len(all_results),
        "successful": len(successful),
        "failed": len(failed),
        "success_rate": round(success_rate, 4),
        "wall_seconds": round(wall_seconds, 3),
        "completed_requests_per_second": round(len(successful) / wall_seconds, 3)
        if wall_seconds
        else 0,
        "max_active_requests": active.maximum,
        "first_token": metric_summary(first_token_values),
        "total_latency": metric_summary(total_values),
        "input_stage": metric_summary(input_stage_values),
        "first_audio": metric_summary(first_audio_values),
        "output_characters": sum(item.output_chars for item in successful),
        "audio_bytes": sum(item.audio_bytes for item in all_results),
        "errors": dict(Counter(item.error or "unknown" for item in failed)),
        "cleanup_errors": cleanup_errors,
        "requests": [asdict(item) for item in all_results],
    }

    print("\n=== 压测结果 ===")
    print(
        f"成功: {len(successful)}/{len(all_results)} "
        f"({success_rate * 100:.2f}%)，失败: {len(failed)}"
    )
    print(
        f"实际峰值并发: {active.maximum}，总耗时: {wall_seconds:.2f}s，"
        f"完成吞吐: {summary['completed_requests_per_second']:.2f} req/s"
    )
    if args.scenario != "tts":
        print_metric("首字延迟", summary["first_token"])
    total_label = {
        "text": "完整回答耗时",
        "image": "图片问答完整链路",
        "voice": "语音问答完整链路",
        "tts": "完整音频耗时",
    }[args.scenario]
    print_metric(total_label, summary["total_latency"])
    if input_stage_values:
        label = "图片上传耗时" if args.scenario == "image" else "语音识别耗时"
        print_metric(label, summary["input_stage"])
    if first_audio_values:
        print_metric("音频首包延迟", summary["first_audio"])
    if failed:
        print("错误分布:")
        for error, count in Counter(item.error or "unknown" for item in failed).most_common():
            print(f"  {count:>3}  {error}")
    if cleanup_errors:
        print(f"警告：{len(cleanup_errors)} 个测试会话清理失败。")

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"详细 JSON 报告: {output.resolve()}")

    if success_rate < args.min_success_rate:
        print(
            f"未达到成功率阈值：{success_rate:.2%} < {args.min_success_rate:.2%}",
            file=sys.stderr,
        )
        return 1
    if args.max_p95_first_token_ms is not None:
        p95 = summary["first_token"]["p95_ms"]
        if p95 is None or p95 > args.max_p95_first_token_ms:
            print(
                f"未达到首字 P95 阈值：{p95}ms > {args.max_p95_first_token_ms}ms",
                file=sys.stderr,
            )
            return 1
    if args.max_p95_first_audio_ms is not None:
        p95 = summary["first_audio"]["p95_ms"]
        if p95 is None or p95 > args.max_p95_first_audio_ms:
            print(
                f"未达到音频首包 P95 阈值：{p95}ms > {args.max_p95_first_audio_ms}ms",
                file=sys.stderr,
            )
            return 1
    return 0


def load_fixture(
    value: str | None,
    media_types: dict[str, str],
    option: str,
) -> Fixture:
    if not value:
        raise ValueError(f"缺少 {option}")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"fixture_not_found:{path}")
    media_type = media_types.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(f"fixture_type_not_supported:{path.suffix}")
    data = path.read_bytes()
    if not data:
        raise ValueError("fixture_empty")
    return Fixture(path.name, media_type, data)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="模拟独立患者并发调用流式问答接口，并统计首字和完整回答延迟。"
    )
    command.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端根地址")
    command.add_argument(
        "--scenario",
        choices=("text", "image", "voice", "tts"),
        default="text",
        help="压测链路：文本、图片+视觉问答、语音识别+问答或流式 TTS",
    )
    command.add_argument("--image-file", help="图片场景使用的非患者测试图片")
    command.add_argument("--audio-file", help="语音场景使用的非患者测试录音")
    command.add_argument(
        "--tts-streaming",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="TTS 场景是否调用流式接口，默认开启",
    )
    command.add_argument("--users", type=int, default=50, help="并发患者数，默认 50")
    command.add_argument("--rounds", type=int, default=1, help="并发轮次，默认 1")
    command.add_argument("--timeout", type=float, default=180, help="单次回答超时秒数")
    command.add_argument("--question", default=DEFAULT_QUESTION, help="每个患者发送的测试问题")
    command.add_argument(
        "--min-success-rate",
        type=float,
        default=0.95,
        help="最低成功率，低于该值时脚本返回失败，默认 0.95",
    )
    command.add_argument(
        "--max-p95-first-token-ms",
        type=float,
        default=None,
        help="可选：首字延迟 P95 上限，单位毫秒",
    )
    command.add_argument(
        "--max-p95-first-audio-ms",
        type=float,
        default=None,
        help="可选：TTS 音频首包延迟 P95 上限，单位毫秒",
    )
    command.add_argument("--output", help="可选：详细 JSON 报告路径")
    command.add_argument("--keep-data", action="store_true", help="保留压测产生的会话")
    command.add_argument("--allow-remote", action="store_true", help="确认允许压测非本机地址")
    command.add_argument("--insecure", action="store_true", help="跳过 HTTPS 证书校验，仅限测试环境")
    return command


def main() -> int:
    args = parser().parse_args()
    if args.users < 1 or args.users > 1000:
        print("--users 必须在 1 到 1000 之间。", file=sys.stderr)
        return 2
    if args.rounds < 1 or args.rounds > 100:
        print("--rounds 必须在 1 到 100 之间。", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("--timeout 必须大于 0。", file=sys.stderr)
        return 2
    if not 0 <= args.min_success_rate <= 1:
        print("--min-success-rate 必须在 0 到 1 之间。", file=sys.stderr)
        return 2
    try:
        return asyncio.run(execute(args))
    except ValueError as exc:
        print(f"压测参数错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("压测已中止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
