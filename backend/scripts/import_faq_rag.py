"""Import and optionally publish a hospital FAQ workbook into the RAG store."""

import argparse
import asyncio
from pathlib import Path

from medical_agent.core.config import get_settings
from medical_agent.core.database import init_db, session_factory
from medical_agent.modules.knowledge.service import import_faq_release


async def run(workbook: Path, name: str, publish: bool, exclude_conflicts: bool) -> None:
    await init_db()
    async with session_factory() as db:
        release = await import_faq_release(
            db,
            workbook_path=workbook,
            release_name=name,
            publish=publish,
            exclude_conflicts=exclude_conflicts,
            settings=get_settings(),
        )
        print(f"release_id={release.id} status={release.status} entries={release.entry_count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--name", default="医院 FAQ")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--exclude-conflicts",
        action="store_true",
        help="Exclude duplicate questions with different answers instead of rejecting the import.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.workbook, args.name, args.publish, args.exclude_conflicts))


if __name__ == "__main__":
    main()
