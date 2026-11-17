"""Mixed patient workload: multimodal chat, streaming TTS, and concurrent session CRUD."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from concurrent_chat import (
    AUDIO_MEDIA_TYPES,
    DEFAULT_QUESTION,
    IMAGE_MEDIA_TYPES,
    ActiveCounter,
    Fixture,
    PreparedUser,
    RequestResult,
    cleanup_user,
    is_local_url,
    load_fixture,
    metric_summary,
    mixed_input_type,
    normalize_base_url,
    percentage_hit,
    prepare_user,
    print_metric,
    run_request,
)


@dataclass(slots=True)
class SessionOperationResult:
    worker: int
    cycle: int
    success: bool
    total_ms: float
    create_ms: float | None = None
    list_ms: float | None = None
    detail_ms: float | None = None
    delete_ms: float | None = None
    failed_stage: str | None = None
    error: str | None = None


async def run_session_operations(
    client: httpx.AsyncClient,
    api_base: str,
    *,
    run_id: str,
    worker: int,
    start_signal: asyncio.Event,
    stop_signal: asyncio.Event,
    timeout_seconds: float,
    interval_seconds: float,
    active: ActiveCounter,
) -> list[SessionOperationResult]:
    await start_signal.wait()
    visitor_id = f"loadtest-{run_id}-session-{worker:03d}"
    cycle = 0
    results: list[SessionOperationResult] = []
    while not stop_signal.is_set() or cycle == 0:
        cycle += 1
        started = time.perf_counter()
        create_ms: float | None = None
        list_ms: float | None = None
        detail_ms: float | None = None
        delete_ms: float | None = None
        stage_name = "create"
        conversation_id = ""
        active.enter()
        try:
            async with asyncio.timeout(timeout_seconds):
                stage = time.perf_counter()
                created = await client.post(
                    f"{api_base}/conversations",
                    headers={"X-Visitor-ID": visitor_id},
                    json={},
                )
                created.raise_for_status()
                raw_created = cast(object, created.json())
                if not isinstance(raw_created, dict):
                    raise TypeError("conversation_payload_invalid")
                conversation_id = str(
                    cast(dict[str, object], raw_created).get("id") or ""
                )
                if not conversation_id:
                    raise RuntimeError("conversation_id_missing")
                create_ms = (time.perf_counter() - stage) * 1000

                stage_name = "list"
                stage = time.perf_counter()
                listed = await client.get(
                    f"{api_base}/conversations",
                    headers={"X-Visitor-ID": visitor_id},
                )
                listed.raise_for_status()
                list_ms = (time.perf_counter() - stage) * 1000

                stage_name = "detail"
                stage = time.perf_counter()
                detail = await client.get(
                    f"{api_base}/conversations/{conversation_id}",
                    headers={"X-Visitor-ID": visitor_id},
                )
                detail.raise_for_status()
                detail_ms = (time.perf_counter() - stage) * 1000

                stage_name = "delete"
                stage = time.perf_counter()
                deleted = await client.delete(
                    f"{api_base}/conversations/{conversation_id}",
                    headers={"X-Visitor-ID": visitor_id},
                )
                if deleted.status_code != 204:
                    raise RuntimeError(f"delete_http_{deleted.status_code}:{deleted.text[:200]}")
                delete_ms = (time.perf_counter() - stage) * 1000
                results.append(
                    SessionOperationResult(
                        worker=worker,
                        cycle=cycle,
                        success=True,
                        total_ms=(time.perf_counter() - started) * 1000,
                        create_ms=create_ms,
                        list_ms=list_ms,
                        detail_ms=detail_ms,
                        delete_ms=delete_ms,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - aggregate every load-test failure.
            if conversation_id:
                with suppress(Exception):
                    async with asyncio.timeout(min(3.0, timeout_seconds)):
                        await client.delete(
                            f"{api_base}/conversations/{conversation_id}",
                            headers={"X-Visitor-ID": visitor_id},
                        )
            results.append(
                SessionOperationResult(
                    worker=worker,
                    cycle=cycle,
                    success=False,
                    total_ms=(time.perf_counter() - started) * 1000,
                    create_ms=create_ms,
                    list_ms=list_ms,
                    detail_ms=detail_ms,
                    delete_ms=delete_ms,
                    failed_stage=stage_name,
                    error=(
                        f"TimeoutError:{stage_name}"
                        if isinstance(exc, TimeoutError)
                        else f"{type(exc).__name__}:{exc}"
                    ),
                )
            )
        finally:
            active.leave()
        if stop_signal.is_set():
            break
        try:
            await asyncio.wait_for(stop_signal.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
    return results


def stage_values(
    rows: list[RequestResult] | list[SessionOperationResult],
    name: str,
) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = getattr(row, name, None)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def summarize_interactions(rows: list[RequestResult]) -> dict[str, Any]:
    successful = [row for row in rows if row.success]
    answers_completed = [
        row
        for row in rows
        if row.answer_done_ms is not None and row.assistant_status in {None, "completed"}
    ]
    tts_requested = [row for row in rows if row.tts_requested]
    tts_completed = [row for row in tts_requested if row.tts_total_ms is not None]
    return {
        "total": len(rows),
        "successful": len(successful),
        "failed": len(rows) - len(successful),
        "success_rate": round(len(successful) / len(rows), 4) if rows else 0,
        "answers_completed": len(answers_completed),
        "answer_success_rate": round(len(answers_completed) / len(rows), 4) if rows else 0,
        "tts_requested": len(tts_requested),
        "tts_completed": len(tts_completed),
        "tts_success_rate": (
            round(len(tts_completed) / len(tts_requested), 4) if tts_requested else None
        ),
        "input_types": dict(Counter(row.input_type for row in rows)),
        # A later TTS failure must not erase completed text/input stages or
        # audio already received. Whole-chain latency still uses successes only.
        "first_token": metric_summary(stage_values(rows, "first_token_ms")),
        "input_stage": metric_summary(stage_values(rows, "input_stage_ms")),
        "answer_done": metric_summary(stage_values(answers_completed, "answer_done_ms")),
        "first_audio": metric_summary(stage_values(rows, "first_audio_ms")),
        "tts_done": metric_summary(stage_values(tts_completed, "tts_total_ms")),
        "whole_chain": metric_summary(stage_values(successful, "total_ms")),
        "audio_bytes": sum(row.audio_bytes for row in rows),
        "tts_segments": sum(row.tts_segments for row in rows),
        "errors": dict(Counter(row.error or "unknown" for row in rows if not row.success)),
    }


def summarize_operations(rows: list[SessionOperationResult]) -> dict[str, Any]:
    successful = [row for row in rows if row.success]
    return {
        "total_cycles": len(rows),
        "successful": len(successful),
        "failed": len(rows) - len(successful),
        "success_rate": round(len(successful) / len(rows), 4) if rows else 0,
        "total_latency": metric_summary(stage_values(successful, "total_ms")),
        "create": metric_summary(stage_values(successful, "create_ms")),
        "list": metric_summary(stage_values(successful, "list_ms")),
        "detail": metric_summary(stage_values(successful, "detail_ms")),
        "delete": metric_summary(stage_values(successful, "delete_ms")),
        "failed_stages": dict(
            Counter(row.failed_stage or "unknown" for row in rows if not row.success)
        ),
        "errors": dict(Counter(row.error or "unknown" for row in rows if not row.success)),
    }


async def execute(args: argparse.Namespace) -> int:
    base_url = normalize_base_url(args.base_url)
    if not is_local_url(base_url) and not args.allow_remote:
        print("拒绝压测远程地址。确认目标后增加 --allow-remote。", file=sys.stderr)
        return 2
    image = (
        load_fixture(args.image_file, IMAGE_MEDIA_TYPES, "--image-file")
        if args.image_percent
        else None
    )
    audio = (
        load_fixture(args.audio_file, AUDIO_MEDIA_TYPES, "--audio-file")
        if args.voice_percent
        else None
    )
    api_base = f"{base_url}/api/v1"
    run_id = uuid.uuid4().hex[:10]
    active = ActiveCounter()
    start_signal = asyncio.Event()
    stop_operations = asyncio.Event()
    cleanup_errors: list[str] = []
    # One patient may hold the chat SSE plus three concurrent TTS prefetch streams.
    max_connections = max(args.users * 4 + args.session_operation_users * 2 + 30, 100)
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max(args.users * 3 + args.session_operation_users, 50),
    )
    timeout = httpx.Timeout(connect=15, read=None, write=30, pool=30)

    print(f"目标: {base_url}")
    print(
        f"混合交互用户: {args.users}，会话操作用户: {args.session_operation_users}，"
        f"输入比例: 文本 {args.text_percent}% / 语音 {args.voice_percent}% / "
        f"图片 {args.image_percent}%，TTS: {args.tts_percent}%"
    )
    print("注意：会调用真实 ASR、Embedding、Chat、Vision 和 TTS，并产生供应商费用。")

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        verify=not args.insecure,
    ) as client:
        health = await client.get(f"{api_base}/health/live")
        health.raise_for_status()
        prepared_raw = await asyncio.gather(
            *(prepare_user(client, api_base, run_id, index) for index in range(1, args.users + 1)),
            return_exceptions=True,
        )
        preparation_errors = [item for item in prepared_raw if isinstance(item, BaseException)]
        prepared = [item for item in prepared_raw if isinstance(item, PreparedUser)]
        if preparation_errors:
            for error in preparation_errors[:10]:
                print(f"会话准备失败: {type(error).__name__}:{error}", file=sys.stderr)
            await asyncio.gather(*(cleanup_user(client, api_base, user) for user in prepared))
            return 2

        interaction_tasks: list[asyncio.Task[RequestResult]] = []
        for user in prepared:
            input_type = mixed_input_type(
                user.index,
                text_percent=args.text_percent,
                voice_percent=args.voice_percent,
            )
            fixture: Fixture | None = image if input_type == "image" else audio if input_type == "voice" else None
            interaction_tasks.append(
                asyncio.create_task(
                    run_request(
                        client,
                        api_base,
                        user,
                        1,
                        args.question,
                        start_signal,
                        args.timeout,
                        active,
                        input_type,
                        fixture,
                        True,
                        speak_response=percentage_hit(
                            user.index,
                            args.tts_percent,
                            salt=11,
                        ),
                    )
                )
            )
        operation_tasks = [
            asyncio.create_task(
                run_session_operations(
                    client,
                    api_base,
                    run_id=run_id,
                    worker=worker,
                    start_signal=start_signal,
                    stop_signal=stop_operations,
                    timeout_seconds=args.session_operation_timeout,
                    interval_seconds=args.session_operation_interval_ms / 1000,
                    active=active,
                )
            )
            for worker in range(1, args.session_operation_users + 1)
        ]

        wall_started = time.perf_counter()
        start_signal.set()
        try:
            interactions = list(await asyncio.gather(*interaction_tasks))
        finally:
            stop_operations.set()
        operation_groups = await asyncio.gather(*operation_tasks)
        wall_seconds = time.perf_counter() - wall_started
        operations = [row for group in operation_groups for row in group]
        if not args.keep_data:
            removed = await asyncio.gather(
                *(cleanup_user(client, api_base, user) for user in prepared)
            )
            cleanup_errors = [item for item in removed if item]

    interaction_summary = summarize_interactions(interactions)
    operation_summary = summarize_operations(operations)
    report: dict[str, Any] = {
        "run_id": run_id,
        "target": base_url,
        "scenario": "mixed",
        "users": args.users,
        "session_operation_users": args.session_operation_users,
        "ratios": {
            "text_percent": args.text_percent,
            "voice_percent": args.voice_percent,
            "image_percent": args.image_percent,
            "tts_percent": args.tts_percent,
        },
        "wall_seconds": round(wall_seconds, 3),
        "max_active_workflows": active.maximum,
        "interactions": interaction_summary,
        "session_operations": operation_summary,
        "cleanup_errors": cleanup_errors,
        "requests": [asdict(row) for row in interactions],
        "operation_cycles": [asdict(row) for row in operations],
    }

    print("\n=== 混合压测结果 ===")
    print(
        f"端到端成功: {interaction_summary['successful']}/{interaction_summary['total']}，"
        f"文字回答完成: {interaction_summary['answers_completed']}/"
        f"{interaction_summary['total']}，TTS 完成: "
        f"{interaction_summary['tts_completed']}/{interaction_summary['tts_requested']}，"
        f"会话操作成功: {operation_summary['successful']}/"
        f"{operation_summary['total_cycles']}，总耗时: {wall_seconds:.2f}s，"
        f"并发业务流程峰值: {active.maximum}"
    )
    print(f"实际输入分布: {interaction_summary['input_types']}")
    print_metric("文本首字延迟", interaction_summary["first_token"])
    print_metric("ASR/图片上传阶段", interaction_summary["input_stage"])
    print_metric("模型回答完成", interaction_summary["answer_done"])
    print_metric("TTS 音频首包", interaction_summary["first_audio"])
    print_metric("TTS 全部分段完成", interaction_summary["tts_done"])
    print_metric("患者完整链路", interaction_summary["whole_chain"])
    print_metric("会话 CRUD 完整周期", operation_summary["total_latency"])
    print_metric("新建会话", operation_summary["create"])
    print_metric("历史列表", operation_summary["list"])
    print_metric("会话详情", operation_summary["detail"])
    print_metric("删除会话", operation_summary["delete"])
    if interaction_summary["errors"]:
        print(f"交互错误: {interaction_summary['errors']}")
    if operation_summary["errors"]:
        print(f"会话操作错误: {operation_summary['errors']}")
        print(f"会话操作失败阶段: {operation_summary['failed_stages']}")
    if cleanup_errors:
        print(f"警告：{len(cleanup_errors)} 个交互测试会话清理失败。")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"详细 JSON 报告: {output.resolve()}")

    interaction_rate = cast(float, interaction_summary["success_rate"])
    operation_rate = cast(float, operation_summary["success_rate"])
    return 0 if (
        interaction_rate >= args.min_success_rate
        and operation_rate >= args.min_operation_success_rate
    ) else 1


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="多模态问答、流式 TTS 与会话 CRUD 混合压测。")
    command.add_argument("--base-url", default="http://127.0.0.1:8000")
    command.add_argument("--users", type=int, default=50, help="同时问答的患者数")
    command.add_argument(
        "--session-operation-users",
        type=int,
        default=10,
        help="问答期间循环新建/读取/删除会话的独立用户数",
    )
    command.add_argument("--text-percent", type=int, default=50)
    command.add_argument("--voice-percent", type=int, default=25)
    command.add_argument("--image-percent", type=int, default=25)
    command.add_argument("--tts-percent", type=int, default=100)
    command.add_argument("--image-file", help="图片比例大于 0 时必填")
    command.add_argument("--audio-file", help="语音比例大于 0 时必填")
    command.add_argument("--question", default=DEFAULT_QUESTION)
    command.add_argument("--timeout", type=float, default=240)
    command.add_argument("--session-operation-timeout", type=float, default=20)
    command.add_argument("--session-operation-interval-ms", type=int, default=2000)
    command.add_argument("--min-success-rate", type=float, default=0.95)
    command.add_argument(
        "--min-operation-success-rate",
        type=float,
        default=0.99,
        help="会话 CRUD 最低成功率，默认 0.99",
    )
    command.add_argument("--output", default=".artifacts/load-mixed.json")
    command.add_argument("--keep-data", action="store_true")
    command.add_argument("--allow-remote", action="store_true")
    command.add_argument("--insecure", action="store_true")
    return command


def validate(args: argparse.Namespace) -> None:
    if not 1 <= args.users <= 1000:
        raise ValueError("--users 必须在 1 到 1000 之间")
    if not 0 <= args.session_operation_users <= 100:
        raise ValueError("--session-operation-users 必须在 0 到 100 之间")
    percentages = [args.text_percent, args.voice_percent, args.image_percent]
    if any(value < 0 or value > 100 for value in percentages) or sum(percentages) != 100:
        raise ValueError("文本、语音和图片比例必须在 0 到 100 之间且合计为 100")
    if not 0 <= args.tts_percent <= 100:
        raise ValueError("--tts-percent 必须在 0 到 100 之间")
    if args.voice_percent and not args.audio_file:
        raise ValueError("语音比例大于 0 时必须提供 --audio-file")
    if args.image_percent and not args.image_file:
        raise ValueError("图片比例大于 0 时必须提供 --image-file")
    if args.timeout <= 0 or args.session_operation_timeout <= 0:
        raise ValueError("超时时间必须大于 0")
    if args.session_operation_interval_ms < 0:
        raise ValueError("会话操作间隔不能小于 0")
    if not 0 <= args.min_success_rate <= 1:
        raise ValueError("--min-success-rate 必须在 0 到 1 之间")
    if not 0 <= args.min_operation_success_rate <= 1:
        raise ValueError("--min-operation-success-rate 必须在 0 到 1 之间")


def main() -> int:
    args = parser().parse_args()
    try:
        validate(args)
        return asyncio.run(execute(args))
    except (ValueError, httpx.HTTPError) as exc:
        print(f"混合压测启动失败：{type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("压测已中止。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
