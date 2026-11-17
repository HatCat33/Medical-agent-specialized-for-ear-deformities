"""Export interaction JSONL records to the hospital FAQ workbook layout."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import copy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from medical_agent.core.config import BACKEND_DIR, backend_path, get_settings
from medical_agent.core.logging import interaction_log_root

HEADERS = (
    "分类",
    "标准问题",
    "其他问法",
    "答案内容",
    "有效日期",
    "关联知识1",
    "关联知识2",
    "关联知识3",
    "关联知识4",
    "关联知识5",
    "关联知识6",
    "关联知识7",
    "关联知识8",
    "关联知识9",
    "关联知识10",
)
CATEGORY = "用户调整知识"
STATE_VERSION = 1


@dataclass(frozen=True)
class Interaction:
    timestamp: datetime
    timestamp_raw: str
    interaction_id: str
    input_text: str
    output_text: str
    status: str
    owner_ref: str = ""

    @property
    def cursor(self) -> tuple[datetime, str]:
        return self.timestamp, self.interaction_id


@dataclass(frozen=True)
class Cursor:
    timestamp: datetime
    interaction_id: str

    @property
    def key(self) -> tuple[datetime, str]:
        return self.timestamp, self.interaction_id


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    return ILLEGAL_CHARACTERS_RE.sub("", text)


def _parse_timestamp(value: object) -> tuple[datetime, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC), raw


def normalize_owner_reference(value: object) -> str:
    normalized = re.sub(r"[-\s]", "", str(value or "")).lower()
    if not re.fullmatch(r"[0-9a-f]{16}", normalized):
        raise ValueError("--user-id 必须是患者端显示的 16 位用户 ID")
    return normalized


def _resolve_log_path(value: str | None) -> Path:
    if value:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    return backend_path(get_settings().interaction_log_file_path)


def _rotation_paths(path: Path) -> list[Path]:
    candidates = [item for item in path.parent.glob(f"{path.name}*") if item.is_file()]

    def order(item: Path) -> tuple[int, str]:
        if item == path:
            return (10**9, item.name)
        match = re.fullmatch(re.escape(path.name) + r"\.(\d+)", item.name)
        return (-int(match.group(1)), item.name) if match else (0, item.name)

    return sorted(candidates, key=order)


def _all_log_paths(path: Path, *, include_rotated: bool) -> list[Path]:
    paths = _rotation_paths(path) if include_rotated else ([path] if path.is_file() else [])
    if include_rotated:
        dated_root = interaction_log_root(path)
        paths.extend(dated_root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.jsonl"))
    return sorted(set(paths), key=lambda item: str(item).casefold())


def _parse_date(value: str | None, option: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{option} 必须使用 YYYY-MM-DD 格式") from exc


def _date_bounds(
    start_date: date | None,
    end_date: date | None,
    timezone: str,
) -> tuple[datetime | None, datetime | None]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("--start-date 不能晚于 --end-date")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知时区: {timezone}") from exc
    start = datetime.combine(start_date, time.min, zone).astimezone(UTC) if start_date else None
    end = datetime.combine(end_date, time.max, zone).astimezone(UTC) if end_date else None
    return start, end


def read_interactions(
    path: Path,
    *,
    include_rotated: bool,
    include_interrupted: bool,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    owner_ref: str | None = None,
) -> tuple[list[Interaction], int]:
    paths = _all_log_paths(path, include_rotated=include_rotated)
    if not paths:
        raise FileNotFoundError(f"Interaction log not found: {path}")
    records: dict[str, Interaction] = {}
    skipped = 0
    for source in paths:
        with source.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    print(
                        f"Warning: ignored invalid JSON at {source.name}:{line_number}",
                        file=sys.stderr,
                    )
                    continue
                if not isinstance(value, dict) or value.get("event") != "conversation_interaction":
                    continue
                status = str(value.get("status") or "")
                if status != "completed" and not (include_interrupted and status == "interrupted"):
                    continue
                input_text = _clean_text(value.get("input_text"))
                output_text = _clean_text(value.get("output_text"))
                parsed_timestamp = _parse_timestamp(value.get("timestamp"))
                interaction_id = _clean_text(value.get("interaction_id"))
                if not input_text or not output_text or parsed_timestamp is None or not interaction_id:
                    skipped += 1
                    continue
                timestamp, timestamp_raw = parsed_timestamp
                record_owner_ref = _clean_text(value.get("owner_ref")).lower()
                if owner_ref is not None and record_owner_ref != owner_ref:
                    continue
                if start_at is not None and timestamp < start_at:
                    continue
                if end_at is not None and timestamp > end_at:
                    continue
                candidate = Interaction(
                    timestamp=timestamp,
                    timestamp_raw=timestamp_raw,
                    interaction_id=interaction_id,
                    input_text=input_text,
                    output_text=output_text,
                    status=status,
                    owner_ref=record_owner_ref,
                )
                previous = records.get(interaction_id)
                if previous is None or candidate.cursor >= previous.cursor:
                    records[interaction_id] = candidate
    return sorted(records.values(), key=lambda item: item.cursor), skipped


def read_cursor(path: Path) -> Cursor | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        parsed = _parse_timestamp(value.get("timestamp")) if isinstance(value, dict) else None
        interaction_id = _clean_text(value.get("interaction_id")) if isinstance(value, dict) else ""
    except (OSError, json.JSONDecodeError):
        return None
    if parsed is None or not interaction_id:
        return None
    return Cursor(timestamp=parsed[0], interaction_id=interaction_id)


def select_interactions(
    records: list[Interaction],
    *,
    mode: Literal["latest", "incremental", "all"],
    limit: int,
    cursor: Cursor | None,
) -> list[Interaction]:
    if mode == "incremental" and cursor is not None:
        records = [item for item in records if item.cursor > cursor.key]
    if mode == "latest":
        return records[-limit:]
    if limit > 0:
        return records[:limit]
    return records


def _new_workbook(sheet_name: str) -> tuple[Workbook, Worksheet]:
    workbook = Workbook()
    sheet = cast(Worksheet, workbook.active)
    sheet.title = sheet_name
    widths = (21, 32.453125, 15.36328125, 67.26953125, 28.1796875) + (13,) * 10
    thin = Side(style="thin", color="FF000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for column, (header, width) in enumerate(zip(HEADERS, widths, strict=True), start=1):
        cell = sheet.cell(1, column, header)
        cell.font = Font(name="宋体", size=11)
        cell.alignment = Alignment(vertical="center")
        cell.border = border
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.row_dimensions[1].height = 42
    sheet.sheet_view.zoomScale = 130
    return workbook, sheet


def _template_workbook(template: Path, sheet_name: str | None) -> tuple[Workbook, Worksheet]:
    workbook = load_workbook(template)
    if not workbook.worksheets:
        raise ValueError("Template workbook has no worksheet")
    sheet = workbook[workbook.sheetnames[0]]
    headers = tuple(_clean_text(sheet.cell(1, column).value) for column in range(1, len(HEADERS) + 1))
    if headers != HEADERS:
        raise ValueError("Template headers do not match the expected hospital FAQ layout")
    if sheet.max_row > 2:
        sheet.delete_rows(3, sheet.max_row - 2)
    if sheet.max_row >= 2:
        for column in range(1, len(HEADERS) + 1):
            sheet.cell(2, column).value = None
    if sheet_name:
        sheet.title = sheet_name
    return workbook, sheet


def _copy_style(source: Any, target: Any) -> None:
    target.font = copy(source.font)
    target.fill = copy(source.fill)
    target.border = copy(source.border)
    target.alignment = copy(source.alignment)
    target.number_format = source.number_format
    target.protection = copy(source.protection)


def populate_sheet(
    sheet: Worksheet,
    records: list[Interaction],
    *,
    timezone: str = "Asia/Shanghai",
) -> None:
    try:
        display_zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知时区: {timezone}") from exc
    style_source_row = 2 if sheet.max_row >= 2 else None
    thin = Side(style="thin", color="FF000000")
    default_border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row_number, item in enumerate(records, start=2):
        local_timestamp = item.timestamp.astimezone(display_zone)
        values = (
            CATEGORY,
            item.input_text,
            "",
            item.output_text,
            f"2088-01-01 {local_timestamp:%H:%M}:{local_timestamp.microsecond // 1000:03d}",
            *("" for _ in range(10)),
        )
        for column, value in enumerate(values, start=1):
            cell = cast(Cell, sheet.cell(row_number, column))
            cell.value = value
            cell.data_type = "s"
            if style_source_row is not None and row_number != style_source_row:
                _copy_style(sheet.cell(style_source_row, column), cell)
            else:
                cell.font = Font(name="宋体", size=11)
                cell.alignment = Alignment(
                    vertical="center",
                    wrap_text=column in {2, 4},
                )
                cell.border = default_border
                if column in {2, 4}:
                    cell.fill = PatternFill(fill_type="solid", fgColor="00FFFFFF")
                    cell.number_format = "@"


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    if not cleaned:
        raise ValueError("Output name is empty after removing invalid filename characters")
    return cleaned if cleaned.lower().endswith(".xlsx") else f"{cleaned}.xlsx"


def _output_path(directory: Path, name: str | None) -> Path:
    filename = _safe_name(name) if name else f"用户交互FAQ-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def _atomic_save_workbook(workbook: Workbook, destination: Path) -> None:
    with NamedTemporaryFile(
        prefix=f".{destination.stem}-",
        suffix=".xlsx.tmp",
        dir=destination.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        workbook.save(temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_cursor(path: Path, record: Interaction) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "version": STATE_VERSION,
        "timestamp": record.timestamp_raw,
        "interaction_id": record.interaction_id,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}-",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-file", help="交互日志路径；默认读取 backend/.env 的配置")
    parser.add_argument(
        "--mode",
        choices=("latest", "incremental", "all"),
        default="latest",
        help="latest=最后若干条；incremental=上次游标之后；all=全部，默认 latest",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="导出条数；latest 默认最后 100 条，incremental/all 设为 0 表示不限",
    )
    parser.add_argument(
        "--state-file",
        default=str(BACKEND_DIR / "data" / "interaction-faq-export-state.json"),
        help="incremental 模式的游标文件，不保存问答正文",
    )
    parser.add_argument("--reset-state", action="store_true", help="忽略并删除已有增量游标")
    parser.add_argument(
        "--output-dir",
        default=str(BACKEND_DIR / "exports" / "interaction-faq"),
        help="Excel 输出目录",
    )
    parser.add_argument("--name", help="输出文件名；不含 .xlsx 也可以")
    parser.add_argument("--template", help="可选 FAQ 模板；使用其首个工作表的样式")
    parser.add_argument("--sheet-name", help="工作表名称；默认模板原名称或当前年月")
    parser.add_argument("--start-date", help="起始日期（含当天），格式 YYYY-MM-DD")
    parser.add_argument("--end-date", help="结束日期（含当天），格式 YYYY-MM-DD")
    parser.add_argument(
        "--user-id",
        help="按患者端侧边栏显示的用户 ID 筛选，可与起止日期组合使用",
    )
    parser.add_argument(
        "--date-timezone",
        default="Asia/Shanghai",
        help="日期区间使用的时区，默认 Asia/Shanghai",
    )
    parser.add_argument(
        "--include-interrupted",
        action="store_true",
        help="同时导出 interrupted 的部分回答；默认只导出 completed",
    )
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="只读旧格式当前文件，不扫描轮转文件和年月日目录",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.limit < 0:
        raise SystemExit("--limit 不能小于 0")
    if arguments.mode == "latest" and arguments.limit == 0:
        raise SystemExit("latest 模式的 --limit 必须大于 0")
    log_path = _resolve_log_path(arguments.log_file)
    try:
        start_at, end_at = _date_bounds(
            _parse_date(arguments.start_date, "--start-date"),
            _parse_date(arguments.end_date, "--end-date"),
            arguments.date_timezone,
        )
        owner_ref = (
            normalize_owner_reference(arguments.user_id) if arguments.user_id else None
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    state_path = Path(arguments.state_file).expanduser().resolve()
    if arguments.reset_state:
        state_path.unlink(missing_ok=True)
    cursor = read_cursor(state_path) if arguments.mode == "incremental" else None
    records, skipped = read_interactions(
        log_path,
        include_rotated=not arguments.current_only,
        include_interrupted=arguments.include_interrupted,
        start_at=start_at,
        end_at=end_at,
        owner_ref=owner_ref,
    )
    selected = select_interactions(
        records,
        mode=arguments.mode,
        limit=arguments.limit,
        cursor=cursor,
    )
    if not selected:
        print("没有符合条件的新交互记录；未生成 Excel，增量游标保持不变。")
        return 0
    template = Path(arguments.template).expanduser().resolve() if arguments.template else None
    sheet_name = arguments.sheet_name or (None if template else datetime.now().strftime("%Y.%m"))
    workbook, sheet = (
        _template_workbook(template, sheet_name)
        if template
        else _new_workbook(sheet_name or datetime.now().strftime("%Y.%m"))
    )
    populate_sheet(sheet, selected, timezone=arguments.date_timezone)
    destination = _output_path(Path(arguments.output_dir).expanduser().resolve(), arguments.name)
    _atomic_save_workbook(workbook, destination)
    if arguments.mode == "incremental":
        _write_cursor(state_path, selected[-1])
    print(f"Excel: {destination}")
    print(f"导出记录: {len(selected)}；可用记录: {len(records)}；跳过记录: {skipped}")
    if arguments.mode == "incremental":
        print(f"增量游标: {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
