"""
Headless runner — discover + analyze + generate messages without interactive review.
Outputs pending-review profiles as JSON for external tooling or CI integration.

Usage:
  python run_headless.py --icp icp1 --limit 5
  python run_headless.py --skip-discover --skip-analyze --limit 10
"""
import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

from linkedin_connection_agent.crew import LinkedInConnectionCrew
from linkedin_connection_agent.utils.scheduler import ConnectionScheduler


@click.command()
@click.option("--icp", default="icp1", show_default=True)
@click.option("--limit", default=5, show_default=True)
@click.option("--skip-discover", is_flag=True, help="Skip profile discovery phase")
@click.option("--skip-analyze", is_flag=True, help="Skip profile analysis phase")
def main(icp, limit, skip_discover, skip_analyze):
    """Headless pipeline: outputs generated messages as JSON."""
    crew = LinkedInConnectionCrew()
    scheduler = ConnectionScheduler()

    if not skip_discover:
        crew.discover_profiles(icp_key=icp, max_per_query=limit)

    if not skip_analyze:
        crew.analyze_profiles(limit=limit)

    crew.generate_messages(limit=limit)

    drafts = scheduler.list_by_status("message_drafted")
    output = [
        {
            "id": r.id,
            "name": r.profile_name,
            "headline": r.profile_headline,
            "url": r.profile_url,
            "message": r.message,
            "message_length": len(r.message or ""),
        }
        for r in drafts
    ]
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
