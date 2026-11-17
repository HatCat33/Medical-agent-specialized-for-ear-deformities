"""Incrementally backfill embeddings for an existing FAQ release."""

import argparse
import asyncio

from medical_agent.core.config import get_settings
from medical_agent.core.database import init_db, session_factory
from medical_agent.modules.knowledge.service import rebuild_release_embeddings


async def run(
    *,
    release_id: str | None,
    batch_size: int | None,
    rebuild_all: bool,
) -> None:
    await init_db()
    settings = get_settings()
    async with session_factory() as db:
        result = await rebuild_release_embeddings(
            db,
            settings=settings,
            release_id=release_id,
            batch_size=batch_size,
            rebuild_all=rebuild_all,
        )
    print(
        f"release_id={result.release_id} name={result.release_name!r} "
        f"total={result.total_entries} existing={result.existing_entries} "
        f"processed={result.processed_entries} remaining={result.remaining_entries} "
        f"dimension={result.vector_dimension or 'unknown'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill missing FAQ embeddings in resumable batches.",
    )
    parser.add_argument(
        "--release-id",
        help="Knowledge release ID. Defaults to the currently published release.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Embedding request batch size. Defaults to EMBEDDING_BATCH_SIZE.",
    )
    parser.add_argument(
        "--rebuild-all",
        action="store_true",
        help="Replace every vector in the release. Use this after changing embedding models.",
    )
    args = parser.parse_args()
    asyncio.run(
        run(
            release_id=args.release_id,
            batch_size=args.batch_size,
            rebuild_all=args.rebuild_all,
        )
    )


if __name__ == "__main__":
    main()
