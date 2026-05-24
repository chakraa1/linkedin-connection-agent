"""
LinkedIn Connection Agent — CLI entry point.

Commands:
  auth              Browser login + OAuth token exchange
  discover          Search LinkedIn for ICP profiles and save to DB
  analyze           Scrape and analyze discovered profiles (visits LinkedIn, downloads PDFs)
  generate-messages Generate personalized 300-char outreach messages
  review            Interactive human approval of generated messages
  send              Send approved connection requests (confirms before sending)
  run               Full pipeline in one command
  list              List all profiles in the pipeline
  stats             Show pipeline counts by status
"""
import os
import sys
from pathlib import Path

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


if __name__ == "__main__":
    cli()
