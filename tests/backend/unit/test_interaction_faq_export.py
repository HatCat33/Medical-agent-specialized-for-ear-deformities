import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from openpyxl import load_workbook


def _script() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "backend" / "scripts" / "export_interactions_faq.py"
    spec = importlib.util.spec_from_file_location("export_interactions_faq", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _record(index: int, *, status: str = "completed") -> dict[str, object]:
    return {
        "event": "conversation_interaction",
        "interaction_id": f"interaction-{index}",
        "timestamp": f"2026-11-12T01:02:03.{index:06d}+00:00",
        "status": status,
        "owner_ref": "a1b2c3d4e5f67890" if index % 2 else "0123456789abcdef",
        "input_text": f"问题{index}",
        "output_text": f"回答{index}",
    }


def test_latest_export_uses_hospital_faq_layout(tmp_path: Path) -> None:
    module = _script()
    log = tmp_path / "interactions.jsonl"
    log.write_text(
        "\n".join(json.dumps(_record(index), ensure_ascii=False) for index in range(1, 4)),
        encoding="utf-8",
    )
    records, skipped = module.read_interactions(
        log, include_rotated=True, include_interrupted=False
    )
    selected = module.select_interactions(records, mode="latest", limit=2, cursor=None)
    workbook, sheet = module._new_workbook("2026.11")
    module.populate_sheet(sheet, selected)
    output = tmp_path / "output.xlsx"
    module._atomic_save_workbook(workbook, output)

    saved = load_workbook(output)
    saved_sheet = saved["2026.11"]
    assert skipped == 0
    assert [saved_sheet.cell(1, column).value for column in range(1, 16)] == list(
        module.HEADERS
    )
    assert saved_sheet.max_row == 3
    assert saved_sheet["A2"].value == "用户调整知识"
    assert saved_sheet["B2"].value == "问题2"
    assert saved_sheet["D3"].value == "回答3"
    assert saved_sheet["E2"].value == "2088-01-01 09:02:000"
    assert saved_sheet["F2"].value is None


def test_incremental_cursor_selects_only_new_records(tmp_path: Path) -> None:
    module = _script()
    records = [
        module.Interaction(
            timestamp=datetime(2026, 11, 12, 1, 2, 3, index, tzinfo=UTC),
            timestamp_raw=f"2026-11-12T01:02:03.{index:06d}+00:00",
            interaction_id=f"interaction-{index}",
            input_text=f"问题{index}",
            output_text=f"回答{index}",
            status="completed",
            owner_ref="a1b2c3d4e5f67890",
        )
        for index in range(1, 4)
    ]
    state = tmp_path / "state.json"
    module._write_cursor(state, records[1])
    selected = module.select_interactions(
        records,
        mode="incremental",
        limit=0,
        cursor=module.read_cursor(state),
    )
    assert [item.interaction_id for item in selected] == ["interaction-3"]
    state_value = json.loads(state.read_text(encoding="utf-8"))
    assert "input_text" not in state_value
    assert "output_text" not in state_value


def test_reader_combines_legacy_rotation_and_dated_logs(tmp_path: Path) -> None:
    module = _script()
    legacy = tmp_path / "interactions.jsonl"
    legacy.write_text(json.dumps(_record(1), ensure_ascii=False), encoding="utf-8")
    rotated = tmp_path / "interactions.jsonl.1"
    rotated.write_text(json.dumps(_record(2), ensure_ascii=False), encoding="utf-8")
    dated = tmp_path / "interactions" / "2026" / "11"
    dated.mkdir(parents=True)
    (dated / "interactions-2026-11-12.jsonl").write_text(
        json.dumps(_record(3), ensure_ascii=False), encoding="utf-8"
    )

    records, skipped = module.read_interactions(
        legacy,
        include_rotated=True,
        include_interrupted=False,
        start_at=datetime(2026, 11, 12, 1, 2, 3, 2, tzinfo=UTC),
    )
    assert skipped == 0
    assert [item.interaction_id for item in records] == ["interaction-2", "interaction-3"]


def test_reader_filters_by_formatted_user_id_and_date(tmp_path: Path) -> None:
    module = _script()
    log = tmp_path / "interactions.jsonl"
    log.write_text(
        "\n".join(json.dumps(_record(index), ensure_ascii=False) for index in range(1, 4)),
        encoding="utf-8",
    )

    owner_ref = module.normalize_owner_reference("A1B2-C3D4-E5F6-7890")
    records, skipped = module.read_interactions(
        log,
        include_rotated=True,
        include_interrupted=False,
        start_at=datetime(2026, 11, 12, 1, 2, 3, 2, tzinfo=UTC),
        owner_ref=owner_ref,
    )

    assert skipped == 0
    assert [item.interaction_id for item in records] == ["interaction-3"]
    assert records[0].owner_ref == "a1b2c3d4e5f67890"


def test_user_id_must_be_a_16_character_hex_reference() -> None:
    module = _script()

    try:
        module.normalize_owner_reference("not-a-user-id")
    except ValueError as exc:
        assert "16 位用户 ID" in str(exc)
    else:
        raise AssertionError("invalid user ID was accepted")
