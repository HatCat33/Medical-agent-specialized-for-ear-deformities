"""Print a read-only quality profile for a hospital FAQ workbook."""

import argparse
import json
from pathlib import Path

from medical_agent.modules.ingestion.faq_xlsx import profile_faq_xlsx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    report = profile_faq_xlsx(args.workbook)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
