"""
LinkedIn Connection Agent — CLI entry point.

Commands:
  auth              Browser login + OAuth token exchange
  discover          Search LinkedIn for ICP profiles and save to DB
  analyze           Scrape and analyze discovered profiles
  generate-messages Generate personalized 300-char outreach messages
  review            Interactive human approval of generated messages
  send              Send approved connection requests (confirms before sending)
  run-pipeline      Full pipeline in one command (discover→analyze→generate[→send])
  export            Export all profiles + messages to Excel for human review
  import-review     Import Shortlisted column from Excel to approve/reject profiles
  list              List all profiles in the pipeline
  stats             Show pipeline counts by status
  reset             Delete all pipeline data and start fresh
"""
import os
import sys
from pathlib import Path

import nest_asyncio
nest_asyncio.apply()

import click
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

from linkedin_connection_agent.crew import LinkedInConnectionCrew
from linkedin_connection_agent.tools.browser_tool import LinkedInBrowser
from linkedin_connection_agent.tools.linkedin_tool import LinkedInAPITool
from linkedin_connection_agent.utils.scheduler import ConnectionScheduler

console = Console()


@click.group()
def cli():
    """LinkedIn Connection Agent — AI-powered personalized outreach pipeline."""
    pass


@cli.command("auth")
def auth():
    """Log in to LinkedIn via browser and exchange OAuth token."""
    email = os.getenv("LINKEDIN_EMAIL", "")
    password = os.getenv("LINKEDIN_PASSWORD", "")
    if not email or not password:
        console.print("[red]Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env[/red]")
        return

    console.print("[bold cyan]Step 1: Browser login[/bold cyan]")
    with LinkedInBrowser(headless=False) as browser:
        success = browser.login(email, password)
    if not success:
        console.print("[red]Browser login failed. Complete 2FA manually if prompted.[/red]")
        return
    console.print("[green]Browser session saved.[/green]")

    console.print("\n[bold cyan]Step 2: OAuth token exchange[/bold cyan]")
    tool = LinkedInAPITool()
    if tool.authenticate():
        console.print("[bold green]Authentication complete.[/bold green]")
    else:
        console.print("[red]OAuth failed. Check LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET.[/red]")


@cli.command("discover")
@click.option("--icp", default="icp1", show_default=True, help="ICP key from icp_config.yaml")
@click.option("--max-per-query", default=10, show_default=True, help="Max profiles per search string")
def discover(icp, max_per_query):
    """Search LinkedIn and discover ICP profiles."""
    LinkedInConnectionCrew().discover_profiles(icp_key=icp, max_per_query=max_per_query)


@cli.command("analyze")
@click.option("--limit", default=10, show_default=True, help="Number of profiles to analyze")
def analyze(limit):
    """Scrape profiles, extract PDF data, and run AI analysis."""
    LinkedInConnectionCrew().analyze_profiles(limit=limit)


@cli.command("generate-messages")
@click.option("--limit", default=10, show_default=True, help="Number of messages to generate")
def generate_messages(limit):
    """Generate personalized outreach messages for analyzed profiles."""
    LinkedInConnectionCrew().generate_messages(limit=limit)


@cli.command("review")
def review():
    """Interactive review and approval of generated messages."""
    LinkedInConnectionCrew().review_messages()


@cli.command("send")
@click.option("--limit", default=20, show_default=True, help="Max requests to send today")
def send(limit):
    """Send approved connection requests via browser automation."""
    console.print(
        f"\n[bold yellow]About to send up to {limit} connection requests.[/bold yellow]\n"
        "LinkedIn recommends no more than 20 per day.\n"
    )
    if not click.confirm("Proceed?", default=True):
        console.print("[dim]Aborted.[/dim]")
        return
    LinkedInConnectionCrew().send_connections(limit=limit)


@cli.command("run")
@click.option("--icp", default="icp1", show_default=True)
@click.option("--max-per-query", default=10, show_default=True)
@click.option("--limit", default=5, show_default=True, help="Profiles to process end-to-end")
def run(icp, max_per_query, limit):
    """Full pipeline: discover → analyze → generate → review → send."""
    crew = LinkedInConnectionCrew()
    console.print("[bold cyan]Starting full LinkedIn connection pipeline...[/bold cyan]\n")
    crew.discover_profiles(icp_key=icp, max_per_query=max_per_query)
    crew.analyze_profiles(limit=limit)
    crew.generate_messages(limit=limit)
    crew.review_messages()
    crew.send_connections(limit=limit)


@cli.command("list")
@click.option("--status", default=None, help="Filter by status")
def list_profiles(status):
    """List all profiles in the pipeline."""
    scheduler = ConnectionScheduler()
    if status:
        profiles = scheduler.list_by_status(status)
        console.print(f"[bold]{len(profiles)} profiles with status '{status}'[/bold]")
        for p in profiles:
            console.print(f"  [{p.id[:8]}] {p.profile_name} — {p.profile_url}")
    else:
        scheduler.list_all()


@cli.command("stats")
def stats():
    """Show pipeline statistics."""
    scheduler = ConnectionScheduler()
    statuses = [
        "discovered", "analyzed", "message_drafted",
        "approved", "sent", "rejected", "failed",
    ]
    console.print("\n[bold cyan]Pipeline Statistics[/bold cyan]\n")
    for status in statuses:
        count = len(scheduler.list_by_status(status))
        bar = "█" * min(count, 30)
        console.print(f"  {status:<18} {count:>4}  [green]{bar}[/green]")
    console.print()


@cli.command("export")
def export():
    """Export all profiles + messages to outputs/profiles_review.xlsx for human review."""
    path = LinkedInConnectionCrew().export_to_excel()
    console.print(f"[bold green]Exported → {path}[/bold green]")
    console.print(
        "\n[dim]Open the file, set [bold]Shortlisted[/bold] = Yes / No for each profile,"
        " then run:[/dim]  python main.py import-review"
    )


@cli.command("import-review")
@click.option("--path", default=None, help="Path to review Excel (auto-detects most recent if omitted)")
def import_review(path):
    """Read profiles_review Excel and approve / reject based on Shortlisted column."""
    try:
        approved, rejected = LinkedInConnectionCrew().import_excel_review(path=path)
        console.print(
            f"\n[bold green]Imported:[/bold green] {approved} approved, {rejected} rejected."
        )
        console.print(
            "[dim]Run[/dim]  python main.py send  [dim]to send approved connection requests.[/dim]"
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")


@cli.command("run-pipeline")
@click.option("--icp", default="icp1", show_default=True, help="ICP key from icp_config.yaml")
@click.option("--discover-limit", default=15, show_default=True, help="Max profiles per search query")
@click.option("--analyze-limit", default=20, show_default=True, help="Profiles to analyze")
@click.option("--message-limit", default=20, show_default=True, help="Messages to generate")
@click.option("--send/--no-send", default=False, show_default=True, help="Send approved requests after generation")
def run_pipeline(icp, discover_limit, analyze_limit, message_limit, send):
    """Full pipeline: discover → analyze → generate messages [→ send].

    \b
    Equivalent to running in sequence:
      python main.py discover --icp icp1 --max-per-query 15
      python main.py analyze  --limit 20
      python main.py generate-messages --limit 20
      [python main.py send  (only with --send flag)]
    """
    crew = LinkedInConnectionCrew()
    console.print("[bold cyan]Starting LinkedIn connection pipeline...[/bold cyan]\n")

    console.print("[bold]Step 1/3 — Discover[/bold]")
    crew.discover_profiles(icp_key=icp, max_per_query=discover_limit)

    console.print("\n[bold]Step 2/3 — Analyze[/bold]")
    crew.analyze_profiles(limit=analyze_limit)

    console.print("\n[bold]Step 3/3 — Generate Messages[/bold]")
    crew.generate_messages(limit=message_limit)

    if send:
        console.print(f"\n[bold yellow]--send flag active. Sending up to {message_limit} approved requests...[/bold yellow]")
        if click.confirm("Proceed with sending?", default=True):
            crew.send_connections(limit=message_limit)
        else:
            console.print("[dim]Send aborted.[/dim]")
    else:
        console.print(
            "\n[dim]--no-send mode (default). Review the Excel, then run:[/dim]\n"
            "  python main.py import-review\n"
            "  python main.py send"
        )


@cli.command("reset")
def reset_pipeline():
    """Delete all pipeline data from the database and start fresh."""
    console.print(
        "\n[bold red]WARNING:[/bold red] This permanently deletes all discovered profiles,"
        " analysis data, and generated messages.\n"
    )
    if not click.confirm("Are you sure you want to reset?", default=False):
        console.print("[dim]Aborted.[/dim]")
        return
    ConnectionScheduler().reset()
    run_id_file = Path("outputs/.current_run_id")
    if run_id_file.exists():
        run_id_file.unlink()
    console.print("[bold green]Pipeline reset complete. Ready for a fresh run.[/bold green]")


if __name__ == "__main__":
    cli()
