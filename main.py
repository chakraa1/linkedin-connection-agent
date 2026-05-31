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
@click.option("--max-per-query", default=10, show_default=True, help="Max top-match profiles to keep per search string")
@click.option("--location", default=None, help="Override region (default: India). E.g. --location 'United Kingdom'")
@click.option("--fresh", is_flag=True, default=False, help="Clear all existing pipeline data before discovering")
def discover(icp, max_per_query, location, fresh):
    """Search LinkedIn and discover ICP profiles (default region: India).

    \b
    With --fresh: wipes the database and run ID so you start with a clean slate.
    Fetches 2× max-per-query candidates per query then keeps only the top STRONG
    ICP matches, so --max-per-query 1 returns the single best-fitting profile.
    """
    if fresh:
        console.print(
            "\n[bold yellow]--fresh:[/bold yellow] This will delete all existing pipeline data.\n"
        )
        if not click.confirm("Clear all pipeline data and start fresh?", default=False):
            console.print("[dim]Aborted.[/dim]")
            return
        ConnectionScheduler().reset()
        run_id_file = Path("outputs/.current_run_id")
        if run_id_file.exists():
            run_id_file.unlink()
        console.print("[green]Pipeline cleared. Starting fresh discovery.[/green]\n")

    LinkedInConnectionCrew().discover_profiles(icp_key=icp, max_per_query=max_per_query, location=location)


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
@click.option("--message-limit", default=20, show_default=True, help="Messages to generate")
@click.option("--location", default=None, help="Override region (default: India). E.g. --location 'United Kingdom'")
@click.option("--send/--no-send", default=False, show_default=True, help="Send approved requests after Excel review (default: no-send)")
def run_pipeline(icp, discover_limit, message_limit, location, send):
    """Full pipeline: discover → generate messages → Excel review → import → [send].

    \b
    Steps:
      Step 1  discover        — search LinkedIn, scrape profiles, filter by seniority
      Step 2  generate-messages — write 250-300 word messages, validate rules A-I
      Step 3  Excel review    — open Excel, set Shortlisted = Yes / No, save
      Step 4  import-review   — sync decisions back to DB
      [send   — only with --send flag]
    """
    crew = LinkedInConnectionCrew()
    console.print("[bold cyan]Starting LinkedIn connection pipeline...[/bold cyan]\n")

    # ── Step 1: Discover ──────────────────────────────────────────────────
    console.print("[bold]Step 1/4 — Discover[/bold]")
    crew.discover_profiles(icp_key=icp, max_per_query=discover_limit, location=location)

    # ── Step 2: Generate Messages ─────────────────────────────────────────
    console.print("\n[bold]Step 2/4 — Generate Messages[/bold]")
    crew.generate_messages(limit=message_limit)

    # ── Step 3: Human Review (Excel) ──────────────────────────────────────
    console.print("\n[bold]Step 3/4 — Human Review (Excel)[/bold]")
    excel_path = crew.export_to_excel()
    console.print(f"[bold green]Excel → {excel_path}[/bold green]")

    try:
        os.startfile(str(excel_path))
        console.print("[dim]Excel opened automatically.[/dim]")
    except Exception:
        import subprocess
        subprocess.Popen(["start", "", str(excel_path)], shell=True)

    console.print(
        "\n[dim]Instructions:[/dim]\n"
        "  1. Set [bold]Shortlisted[/bold] = Yes / No for each profile\n"
        "  2. Edit the message directly in the cell if needed\n"
        "  3. Save and close the file\n"
    )
    click.pause("  Press any key once you have saved the Excel review...")

    # ── Step 4: Import Review ─────────────────────────────────────────────
    console.print("\n[bold]Step 4/4 — Import Review[/bold]")
    try:
        approved, rejected = crew.import_excel_review()
        console.print(
            f"[bold green]Imported:[/bold green] {approved} approved, {rejected} rejected."
        )
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    # ── Optional: Send ────────────────────────────────────────────────────
    if send:
        console.print(f"\n[bold yellow]--send flag active.[/bold yellow]")
        if click.confirm(f"Send up to {message_limit} approved connection requests?", default=False):
            crew.send_connections(limit=message_limit)
        else:
            console.print("[dim]Send skipped.[/dim]")
    else:
        console.print(
            "\n[dim]--no-send (default). To send approved requests:[/dim]\n"
            "  python main.py send"
        )


@cli.command("create-search-config")
@click.argument("excel_path")
@click.option("--output", default="config/search_strings.yaml", show_default=True,
              help="Destination YAML file")
def create_search_config(excel_path, output):
    """Convert a stage1 search-strings Excel to config/search_strings.yaml.

    \b
    EXCEL_PATH  Path to a stage1_search_strings_*.xlsx file.
                Columns expected: #  |  Query  |  Rationale  |  Segment

    Once the YAML exists, 'discover' loads it instead of calling the LLM,
    keeping search behaviour consistent across runs.
    """
    import openpyxl, yaml as _yaml
    from pathlib import Path as _Path

    src = _Path(excel_path)
    if not src.exists():
        console.print(f"[red]File not found: {excel_path}[/red]")
        return

    wb = openpyxl.load_workbook(str(src))
    ws = wb.active
    entries = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        num, query, rationale, segment = (list(row) + [None] * 4)[:4]
        if query and str(query).strip():
            entries.append({
                "segment":   str(segment or "").strip(),
                "query":     str(query).strip(),
                "rationale": str(rationale or "").strip(),
            })

    if not entries:
        console.print("[red]No query rows found in the Excel.[/red]")
        return

    doc = {
        "_source": str(src),
        "_note": "Curated Boolean search strings. When this file exists, discover skips LLM generation.",
        "search_strings": entries,
    }
    out = _Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        _yaml.dump(doc, allow_unicode=True, sort_keys=False, width=120, default_flow_style=False),
        encoding="utf-8",
    )
    console.print(f"[bold green]Saved {len(entries)} queries → {output}[/bold green]")
    by_seg: dict[str, int] = {}
    for e in entries:
        by_seg[e["segment"]] = by_seg.get(e["segment"], 0) + 1
    for seg, n in by_seg.items():
        console.print(f"  [dim]{seg}: {n}[/dim]")


@cli.command("init-persona")
@click.option("--name", prompt="Your full name", help="Your name")
@click.option("--role", prompt="Your current role / title", help="e.g. Content Marketing Lead")
@click.option("--company", prompt="Your company", help="e.g. Stealth AI Startup")
@click.option("--background", prompt="Your background (1-2 sentences)", help="What you bring to the conversation")
@click.option("--linkedin-url", prompt="Your LinkedIn URL", help="https://www.linkedin.com/in/yourhandle/")
@click.option(
    "--goal",
    type=click.Choice(["PEER_COLLABORATION", "CLIENT_ACQUISITION", "TALENT_ACQUISITION",
                        "JOB_HUNTING", "INVESTOR_OUTREACH"], case_sensitive=False),
    prompt="Outreach goal",
    help="What you want to achieve with these connections",
)
@click.option("--location", default="India", show_default=True, help="Target location")
def init_persona(name, role, company, background, linkedin_url, goal, location):
    """Create config/persona.yaml from a simple prompt — replaces all hardcoded sender info."""
    import yaml as _yaml
    from pathlib import Path as _Path

    goal_framing = {
        "PEER_COLLABORATION":  "Building a warm peer network for knowledge exchange and mutual learning.",
        "CLIENT_ACQUISITION":  "Building warm relationships with potential clients before any commercial conversation.",
        "TALENT_ACQUISITION":  "Connecting with talented people who might be a great fit for open roles.",
        "JOB_HUNTING":         "Exploring opportunities by genuinely engaging with people doing interesting work.",
        "INVESTOR_OUTREACH":   "Connecting with investors whose thesis aligns with your work.",
    }

    tone_map = {
        "PEER_COLLABORATION":  "peer",
        "CLIENT_ACQUISITION":  "consultant",
        "TALENT_ACQUISITION":  "recruiter",
        "JOB_HUNTING":         "job_seeker",
        "INVESTOR_OUTREACH":   "peer",
    }

    persona = {
        "sender": {
            "name": name,
            "role": role,
            "company": company,
            "background": background,
            "linkedin_url": linkedin_url,
        },
        "goal": goal.upper(),
        "goal_description": goal_framing[goal.upper()],
        "outreach_tone": tone_map[goal.upper()],
        "message_context": (
            f"{name} wants to connect with relevant professionals in a genuine, "
            f"non-transactional way. Messages should reflect curiosity about the "
            f"recipient's work, not pitch {name}'s services or background."
        ),
        "target": {
            "description": f"Relevant professionals for {name}'s goal: {goal}",
            "roles": [],
            "industries": [],
            "keywords": [],
            "locations": [location],
            "connection_degree": "2nd",
            "daily_connection_limit": 20,
        },
        "segments": [
            {"name": "Primary Target", "description": f"Core audience for {goal} goal", "priority": 3},
            {"name": "Secondary Target", "description": "Adjacent audience worth connecting with", "priority": 2},
        ],
    }

    out = _Path("config/persona.yaml")
    out.write_text(
        _yaml.dump(persona, allow_unicode=True, sort_keys=False, default_flow_style=False,
                   width=100),
        encoding="utf-8",
    )
    console.print(f"\n[bold green]Persona saved → {out}[/bold green]")
    console.print(
        "\n[dim]Next steps:[/dim]\n"
        "  1. Open [bold]config/persona.yaml[/bold] and fill in target.roles, "
        "target.industries, target.keywords\n"
        "  2. Delete [bold]config/search_strings.yaml[/bold] so queries are regenerated for your persona\n"
        "  3. Run: [bold]python main.py discover --fresh[/bold]\n"
        "     The agent will generate boolean queries, auto-test them, and only use ones that return results."
    )


@cli.command("reset")
@click.option("--all", "wipe_all", is_flag=True, default=False,
              help="Also delete downloaded PDFs and all Excel review files")
def reset_pipeline(wipe_all):
    """Delete all pipeline data from the database and start fresh."""
    extras = " and all downloaded PDFs + Excel files" if wipe_all else ""
    console.print(
        f"\n[bold red]WARNING:[/bold red] This permanently deletes all discovered profiles,"
        f" analysis data, and generated messages{extras}.\n"
    )
    if not click.confirm("Are you sure you want to reset?", default=False):
        console.print("[dim]Aborted.[/dim]")
        return

    ConnectionScheduler().reset()

    run_id_file = Path("outputs/.current_run_id")
    if run_id_file.exists():
        run_id_file.unlink()

    if wipe_all:
        import shutil
        pdf_dir = Path("outputs/profiles/pdfs")
        if pdf_dir.exists():
            shutil.rmtree(str(pdf_dir))
            pdf_dir.mkdir(parents=True, exist_ok=True)
            console.print("[dim]PDFs cleared.[/dim]")
        for xlsx in Path("outputs").rglob("profiles_review_*.xlsx"):
            xlsx.unlink()
            console.print(f"[dim]Removed {xlsx}[/dim]")

    console.print("[bold green]Pipeline reset complete. Ready for a fresh run.[/bold green]")


if __name__ == "__main__":
    cli()
