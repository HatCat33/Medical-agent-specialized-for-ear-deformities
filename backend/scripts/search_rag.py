"""Run a diagnostic search against the published knowledge release."""

import argparse
import asyncio

from medical_agent.core.config import get_settings
from medical_agent.core.database import init_db, session_factory
from medical_agent.modules.knowledge.retriever import retrieve


async def run(query: str) -> None:
    await init_db()
    async with session_factory() as db:
        hits = await retrieve(db, query=query, settings=get_settings())
        for index, hit in enumerate(hits, start=1):
            print(
                f"[{index}] score={hit.score:.4f} category={hit.category} "
                f"sheet={hit.source_sheet} row={hit.source_row} question={hit.question}"
            )
        if not hits:
            print("no_hits")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    args = parser.parse_args()
    asyncio.run(run(args.query))


if __name__ == "__main__":
    main()
