from pathlib import Path

from medical_agent.core.config import BACKEND_DIR, Settings, backend_path


def test_relative_runtime_paths_are_resolved_from_backend(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings.model_construct(
        log_file_path="./logs/medical-agent.jsonl",
        interaction_log_file_path="./logs/interactions.jsonl",
    )

    assert Path(settings.resolved_log_file_path) == BACKEND_DIR / "logs" / "medical-agent.jsonl"
    assert Path(settings.resolved_interaction_log_file_path) == (
        BACKEND_DIR / "logs" / "interactions.jsonl"
    )


def test_absolute_runtime_path_is_preserved(tmp_path: Path) -> None:
    target = tmp_path / "custom.jsonl"
    assert backend_path(str(target)) == target.resolve()
