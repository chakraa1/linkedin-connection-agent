"""
LinkedIn Connection Crew — 6-agent pipeline for personalized outreach.

Phase 1: Generate Boolean search strings  (boolean_search_agent)
Phase 2: Discover profiles               (Playwright browser automation)
Phase 3: Analyze profiles + posts        (profile_analyzer_agent, post_analyzer_agent)
Phase 4: Generate outreach messages      (message_writer_agent)
Phase 5: Validate messages               (message_validator_agent + MessageValidator)
Phase 6: Human review                    (interactive Rich CLI)
Phase 7: Send connection requests        (Playwright browser automation)
"""
import json
import os
import uuid
from datetime import date as _date
from pathlib import Path

import yaml
from crewai import Agent, Crew, Process, Task
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from linkedin_connection_agent.tools.browser_tool import LinkedInBrowser
# from linkedin_connection_agent.tools.pdf_tool import extract_pdf_text  # PDF disabled
from linkedin_connection_agent.utils.llm_factory import LLMFactory
from linkedin_connection_agent.utils.message_validator import MessageValidator
from linkedin_connection_agent.utils.scheduler import ConnectionScheduler

console = Console()

_BASE = Path(__file__).parent.parent.parent
AGENTS_CFG = yaml.safe_load((_BASE / "config/agents.yaml").read_text(encoding="utf-8"))
TASKS_CFG = yaml.safe_load((_BASE / "config/tasks.yaml").read_text(encoding="utf-8"))
ICP_CFG = yaml.safe_load((_BASE / "config/icp_config.yaml").read_text(encoding="utf-8"))

_RUN_ID_FILE = Path("outputs/.current_run_id")

# Keywords that indicate a senior title — used to filter search results
_SENIOR_TITLE_KEYWORDS = [
    "vp ",          # VP / SVP / EVP all contain "vp " as substring when padded
    "vice president",
    "managing director",
    "md ",          # "MD | Goldman" or "MD Technology"
    "executive director",
    "cto", "cio", "coo", "ceo", "cxo",
    "chief technology", "chief information", "chief operating",
    "chief executive", "chief digital", "chief data",
    "head of engineering", "head of platform", "head of infrastructure",
    "head of technology", "head of it", "head of cloud",
    "director of engineering", "director of technology",
    "director of platform", "director of infrastructure",
    "engineering director", "technology director",
]


def _is_senior(headline: str) -> bool:
    h = " " + headline.lower() + " "
    return any(kw in h for kw in _SENIOR_TITLE_KEYWORDS)


def _parse_relevance(hooks_text: str) -> str:
    """Extract HIGH / MEDIUM / LOW from analyzed hooks output."""
    if not hooks_text:
        return ""
    for line in hooks_text.split("\n"):
        if "RELEVANCE:" in line.upper():
            upper = line.upper()
            if "HIGH" in upper:
                return "HIGH"
            if "MEDIUM" in upper:
                return "MEDIUM"
            if "LOW" in upper:
                return "LOW"
    return ""


def _load_or_create_run_id(new_run: bool = False) -> str:
    """Persist run ID in outputs/.current_run_id so all pipeline steps share one Excel."""
    if new_run or not _RUN_ID_FILE.exists():
        run_id = uuid.uuid4().hex[:8]
        _RUN_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RUN_ID_FILE.write_text(run_id)
        return run_id
    content = _RUN_ID_FILE.read_text().strip()
    return content if content else _load_or_create_run_id(new_run=True)


class LinkedInConnectionCrew:
    def __init__(self):
        self._llm = LLMFactory()
        self._scheduler = ConnectionScheduler()
        self._validator = MessageValidator()
        self._run_id = _load_or_create_run_id(new_run=False)

    # ------------------------------------------------------------------ #
    # Phase 1: Boolean search string generation
    # ------------------------------------------------------------------ #

    def generate_search_strings(self, icp_key: str = "icp1") -> list[dict]:
        icp = ICP_CFG[icp_key]
        agent = Agent(
            role=AGENTS_CFG["boolean_search_agent"]["role"],
            goal=AGENTS_CFG["boolean_search_agent"]["goal"],
            backstory=AGENTS_CFG["boolean_search_agent"]["backstory"],
            llm=self._llm.get("boolean_search_agent"),
            verbose=False,
        )
        task = Task(
            description=TASKS_CFG["generate_boolean_search_task"]["description"].format(
                target_profile_description=icp.get("target_profile_description", icp["description"]),
                target_roles=json.dumps(icp["target_roles"]),
                industries=json.dumps(icp["industries"]),
                locations=json.dumps(icp["locations"]),
                keywords=json.dumps(icp["keywords"]),
            ),
            expected_output=TASKS_CFG["generate_boolean_search_task"]["expected_output"],
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        output = str(crew.kickoff())
        try:
            start, end = output.find("["), output.rfind("]") + 1
            return json.loads(output[start:end])
        except Exception:
            return [{"query": output.strip(), "rationale": "raw output"}]

    # ------------------------------------------------------------------ #
    # Phase 2: Profile discovery
    # ------------------------------------------------------------------ #

    def discover_profiles(self, icp_key: str = "icp1", max_per_query: int = 10) -> int:
        # Each discover run starts a fresh Excel with a new run ID
        self._run_id = _load_or_create_run_id(new_run=True)

        search_strings = self.generate_search_strings(icp_key)
        console.print(f"\n[bold cyan]Generated {len(search_strings)} search strings.[/bold cyan]")
        for i, s in enumerate(search_strings, 1):
            segment = s.get("segment", "")
            label = f"[dim]{segment}[/dim] " if segment else ""
            console.print(f"  {i}. {label}{s['query'][:80]}...")

        skipped_junior = 0
        skipped_existing = 0
        pending_scrape: list[dict] = []   # new senior profiles not yet in DB
        seen_urls: set[str] = set()       # cross-query dedup within this run

        with LinkedInBrowser(headless=False) as browser:
            if not browser.is_logged_in():
                console.print("[yellow]Session expired — logging in with .env credentials...[/yellow]")
                if not browser.login(os.environ["LINKEDIN_EMAIL"], os.environ["LINKEDIN_PASSWORD"]):
                    console.print("[red]Login failed. Check LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env[/red]")
                    return 0

            # ── Phase 1: collect search results ──────────────────────────
            session_refreshed = False
            for search in search_strings:
                console.print(f"\n[dim]Searching: {search['query'][:60]}...[/dim]")
                try:
                    profiles = browser.search_people(search["query"], max_results=max_per_query)
                except RuntimeError as exc:
                    if "session expired" in str(exc) and not session_refreshed:
                        console.print("[yellow]Session expired mid-run — re-logging in...[/yellow]")
                        if not browser.login(os.environ["LINKEDIN_EMAIL"], os.environ["LINKEDIN_PASSWORD"]):
                            console.print("[red]Re-login failed.[/red]")
                            break
                        session_refreshed = True
                        profiles = browser.search_people(search["query"], max_results=max_per_query)
                    else:
                        console.print(f"[red]Search failed: {exc}[/red]")
                        continue

                for p in profiles:
                    headline = p.get("headline", "") or ""

                    # Filter: only senior titles
                    if not _is_senior(headline):
                        skipped_junior += 1
                        continue

                    url = p["url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    # Gap C fix: skip ANY profile already in the DB (any status —
                    # discovered, analyzed, pending, approved, sent, rejected, failed)
                    if self._scheduler.get_by_url(url):
                        skipped_existing += 1
                        continue

                    pending_scrape.append(p)

            # ── Phase 2: scrape About + Experience for each new profile ───
            # This populates the Excel columns immediately after discovery.
            if pending_scrape:
                console.print(
                    f"\n[dim]Scraping About + Experience for {len(pending_scrape)} new profiles...[/dim]"
                )
                for p in pending_scrape:
                    console.print(f"  Scraping: [cyan]{p['name']}[/cyan]")
                    try:
                        scraped = browser.scrape_profile(p["url"])
                    except Exception as exc:
                        console.print(f"    [yellow]Scrape failed: {exc} — saving basic info only.[/yellow]")
                        scraped = {}

                    profile_data = json.dumps({
                        "name": scraped.get("name") or p["name"],
                        "headline": scraped.get("headline") or p.get("headline", ""),
                        "url": p["url"],
                        "about": scraped.get("about", ""),
                        "experience": scraped.get("experience", []),
                    }, indent=2)

                    self._scheduler.save_discovered(
                        profile_url=p["url"],
                        profile_name=p["name"],
                        profile_headline=p.get("headline", ""),
                        icp_key=icp_key,
                        profile_data=profile_data,
                    )

        new_count = len(pending_scrape)
        console.print(
            f"\n[bold green]Discovered {new_count} senior profiles.[/bold green]"
            f"  [dim](skipped {skipped_junior} junior, {skipped_existing} already in pipeline)[/dim]"
        )

        # Export to Excel immediately — About + Experience now populated
        path = self.export_to_excel()
        console.print(f"[bold cyan]Discovery snapshot → {path}[/bold cyan]")
        return new_count

    # ------------------------------------------------------------------ #
    # Phase 3: Profile analysis (PDF-first, no posts)
    # ------------------------------------------------------------------ #

    def analyze_profiles(self, limit: int = 10) -> int:
        discovered = self._scheduler.list_by_status("discovered")[:limit]
        if not discovered:
            console.print("[yellow]No discovered profiles to analyze.[/yellow]")
            return 0

        # Phase A — browser: scrape posts only (About+Experience already in DB from discovery)
        scraped_data: list[tuple] = []   # (record, profile_data_str, posts)
        console.print("[dim]Phase 1/2: Scraping recent posts...[/dim]")
        with LinkedInBrowser(headless=False) as browser:
            if not browser.is_logged_in():
                console.print("[yellow]Session expired — logging in...[/yellow]")
                if not browser.login(os.environ["LINKEDIN_EMAIL"], os.environ["LINKEDIN_PASSWORD"]):
                    console.print("[red]Login failed.[/red]")
                    return 0

            for record in discovered:
                console.print(f"  Posts: [cyan]{record.profile_name}[/cyan]", end="")

                # Reuse About+Experience from discovery; only re-scrape if missing
                profile_data_str = record.profile_data or ""
                if not profile_data_str:
                    console.print(" [dim](re-scraping profile — not cached)[/dim]")
                    try:
                        scraped = browser.scrape_profile(record.profile_url)
                    except Exception:
                        scraped = {}
                    profile_data_str = json.dumps({
                        "name": scraped.get("name") or record.profile_name,
                        "headline": scraped.get("headline") or record.profile_headline,
                        "url": record.profile_url,
                        "about": scraped.get("about", ""),
                        "experience": scraped.get("experience", []),
                    }, indent=2)
                else:
                    console.print(" [dim](profile cached)[/dim]")

                # Stage 2.2 feed: scrape recent posts
                posts = browser.scrape_recent_posts(record.profile_url)
                post_label = f"[green]{len(posts)} posts[/green]" if posts else "[dim]no posts[/dim]"
                console.print(f"    → {post_label}")
                scraped_data.append((record, profile_data_str, posts))

        # Phase B — CrewAI analysis (browser fully closed, no event loop conflict)
        console.print("\n[dim]Phase 2/2: Running AI analysis...[/dim]")
        profile_analyzer = Agent(
            role=AGENTS_CFG["profile_analyzer_agent"]["role"],
            goal=AGENTS_CFG["profile_analyzer_agent"]["goal"],
            backstory=AGENTS_CFG["profile_analyzer_agent"]["backstory"],
            llm=self._llm.get("profile_analyzer_agent"),
            verbose=False,
        )
        post_analyzer = Agent(
            role=AGENTS_CFG["post_analyzer_agent"]["role"],
            goal=AGENTS_CFG["post_analyzer_agent"]["goal"],
            backstory=AGENTS_CFG["post_analyzer_agent"]["backstory"],
            llm=self._llm.get("post_analyzer_agent"),
            verbose=False,
        )

        processed = 0
        for record, profile_data_str, posts in scraped_data:
            console.print(f"  Analyzing: [cyan]{record.profile_name}[/cyan]")

            # Stage 2.1 — Relevance scoring + profile hooks (About + Experience)
            profile_task = Task(
                description=TASKS_CFG["analyze_profile_task"]["description"].format(
                    profile_data=profile_data_str,
                    profile_name=record.profile_name,
                ),
                expected_output=TASKS_CFG["analyze_profile_task"]["expected_output"].format(
                    profile_name=record.profile_name,
                ),
                agent=profile_analyzer,
            )
            Crew(
                agents=[profile_analyzer],
                tasks=[profile_task],
                process=Process.sequential,
                verbose=False,
            ).kickoff()

            analyzed_hooks = (
                str(profile_task.output)
                if hasattr(profile_task, "output") and profile_task.output
                else profile_data_str
            )

            # Stage 2.2 — Post hook (only when active posts exist)
            if posts:
                post_text = "\n\n---\n\n".join(posts)
                post_task = Task(
                    description=TASKS_CFG["analyze_posts_task"]["description"].format(
                        recent_posts=post_text,
                    ),
                    expected_output=TASKS_CFG["analyze_posts_task"]["expected_output"],
                    agent=post_analyzer,
                )
                Crew(
                    agents=[post_analyzer],
                    tasks=[post_task],
                    process=Process.sequential,
                    verbose=False,
                ).kickoff()
                post_hook_out = (
                    str(post_task.output)
                    if hasattr(post_task, "output") and post_task.output
                    else ""
                )
                if post_hook_out and "NO_POSTS_AVAILABLE" not in post_hook_out:
                    analyzed_hooks = analyzed_hooks + "\n\n## Post Hook\n" + post_hook_out
                    console.print("    [green]Post hook appended.[/green]")
            else:
                console.print("    [dim]Stage 2.2 skipped — no posts.[/dim]")

            self._scheduler.save_analyzed(
                profile_id=record.id,
                profile_data=profile_data_str,
                recent_posts=analyzed_hooks,
                pdf_path="",
            )
            relevance = _parse_relevance(analyzed_hooks)
            rel_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(relevance, "dim")
            console.print(f"    → [{rel_color}]RELEVANCE: {relevance or 'unknown'}[/{rel_color}]")
            processed += 1

        console.print(f"\n[bold green]Analyzed {processed} profiles.[/bold green]")

        # Re-export Excel: hooks, relevance, and auto-shortlist updated
        path = self.export_to_excel()
        console.print(f"[bold cyan]Analysis snapshot → {path}[/bold cyan]")
        return processed

    # ------------------------------------------------------------------ #
    # Phase 4 & 5: Message generation + validation
    # ------------------------------------------------------------------ #

    def generate_messages(self, limit: int = 10) -> int:
        analyzed = self._scheduler.list_by_status("analyzed")[:limit]
        if not analyzed:
            console.print("[yellow]No analyzed profiles ready for message generation.[/yellow]")
            return 0

        message_writer = Agent(
            role=AGENTS_CFG["message_writer_agent"]["role"],
            goal=AGENTS_CFG["message_writer_agent"]["goal"],
            backstory=AGENTS_CFG["message_writer_agent"]["backstory"],
            llm=self._llm.get("message_writer_agent"),
            verbose=False,
        )
        message_validator = Agent(
            role=AGENTS_CFG["message_validator_agent"]["role"],
            goal=AGENTS_CFG["message_validator_agent"]["goal"],
            backstory=AGENTS_CFG["message_validator_agent"]["backstory"],
            llm=self._llm.get("message_validator_agent"),
            verbose=False,
        )

        generated = 0
        for record in analyzed:
            console.print(f"\n  Generating message for: [cyan]{record.profile_name}[/cyan]")

            write_task = Task(
                description=TASKS_CFG["write_message_task"]["description"].format(
                    profile_hooks=(record.recent_posts or record.profile_data or "")[:2000],
                    post_hook="",
                ),
                expected_output=TASKS_CFG["write_message_task"]["expected_output"],
                agent=message_writer,
            )
            validate_task = Task(
                description=TASKS_CFG["validate_message_task"]["description"].format(
                    message="{output_from_write_task}",
                ),
                expected_output=TASKS_CFG["validate_message_task"]["expected_output"],
                agent=message_validator,
                context=[write_task],
            )
            crew = Crew(
                agents=[message_writer, message_validator],
                tasks=[write_task, validate_task],
                process=Process.sequential,
                verbose=False,
            )
            crew.kickoff()

            raw_output = str(write_task.output) if hasattr(write_task, "output") else ""
            message = self._extract_message(raw_output)

            # Local quality gate — auto-revise up to 2x
            for attempt in range(2):
                result = self._validator.validate(message)
                if result.passed:
                    break
                if attempt == 0:
                    console.print(
                        f"    [yellow]Validation issues ({len(result.issues)}): "
                        f"{result.issues[0]}...[/yellow] Revising..."
                    )
                    message = self._revise_message(message, result.issues)

            self._scheduler.save_message(record.id, message)
            generated += 1

        console.print(f"\n[bold green]Generated messages for {generated} profiles.[/bold green]")
        path = self.export_to_excel()
        console.print(f"[bold cyan]Review file saved → {path}[/bold cyan]")
        return generated

    # ------------------------------------------------------------------ #
    # Excel export / import
    # ------------------------------------------------------------------ #

    def export_to_excel(self, out_path: str | None = None) -> str:
        """Export all profiles + messages to Excel for human review.

        Path: outputs/YYYY-MM-DD/profiles_review_<run_id>.xlsx
        Columns (12): Name, Headline, URL, About, Experience, Relevance, Hooks,
                      Generated Message, Char Count, Status, Shortlisted, Notes
        """
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        if out_path is None:
            today = _date.today().strftime("%Y-%m-%d")
            out_path = f"outputs/{today}/profiles_review_{self._run_id}.xlsx"

        STATUS_COLORS = {
            "discovered":     "D9EAD3",
            "analyzed":       "C9DAF8",
            "message_drafted":"FFF2CC",
            "approved":       "B6D7A8",
            "sent":           "6AA84F",
            "rejected":       "F4CCCC",
            "failed":         "EA9999",
        }
        HEADER_FILL = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Outreach Review"

        headers = [
            "Name", "Headline", "LinkedIn URL",
            "About", "Experience",
            "Relevance", "Hooks",
            "Generated Message", "Char Count",
            "Status", "Shortlisted (Yes / No)", "Notes",
        ]
        col_widths = [28, 40, 48, 55, 55, 12, 60, 65, 10, 16, 22, 35]

        for col, (header, width) in enumerate(zip(headers, col_widths), 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.column_dimensions[get_column_letter(col)].width = width

        ws.row_dimensions[1].height = 28

        for row_idx, profile in enumerate(self._scheduler.list_all_records(), 2):
            about = ""
            experience = ""
            try:
                pd = json.loads(profile.profile_data or "{}")
                about = pd.get("about", "") or ""
                exp_list = pd.get("experience", []) or []
                experience = "\n\n".join(e.strip() for e in exp_list if e.strip())
            except Exception:
                pass

            relevance = _parse_relevance(profile.recent_posts or "")
            hooks_text = (profile.recent_posts or "")[:3000]

            # Auto-shortlist: HIGH relevance profiles not yet rejected
            if profile.status in ("approved", "sent"):
                shortlisted = "Yes"
            elif profile.status == "rejected":
                shortlisted = "No"
            elif relevance == "HIGH" and profile.status not in ("rejected",):
                shortlisted = "Yes"
            else:
                shortlisted = ""

            row_data = [
                profile.profile_name or "",
                profile.profile_headline or "",
                profile.profile_url or "",
                about[:2000],
                experience[:2000],
                relevance,
                hooks_text,
                profile.message or "",
                len(profile.message or ""),
                profile.status or "",
                shortlisted,
                "",
            ]
            row_fill = PatternFill(
                start_color=STATUS_COLORS.get(profile.status, "FFFFFF"),
                end_color=STATUS_COLORS.get(profile.status, "FFFFFF"),
                fill_type="solid",
            )
            WRAP_COLS = {4, 5, 7, 8}  # About, Experience, Hooks, Generated Message
            for col, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=value)
                cell.fill = row_fill
                cell.alignment = Alignment(vertical="top", wrap_text=(col in WRAP_COLS))

        ws.freeze_panes = "A2"

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(out))
        return str(out)

    def import_excel_review(self, path: str | None = None) -> tuple[int, int]:
        """
        Read profiles_review Excel (auto-detects most recent if path not specified).
        Shortlisted = 'Yes'  → approve (and update message if edited).
        Shortlisted = 'No'   → reject.
        Blank                → leave as-is.
        Returns (approved_count, rejected_count).
        """
        import openpyxl

        xlsx_path: Path
        if path:
            xlsx_path = Path(path)
        else:
            candidates = sorted(
                Path("outputs").rglob("profiles_review_*.xlsx"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                xlsx_path = candidates[0]
            else:
                xlsx_path = Path("outputs/profiles_review.xlsx")

        if not xlsx_path.exists():
            raise FileNotFoundError(
                f"No review Excel found at {xlsx_path}. Run: python main.py export"
            )

        console.print(f"[dim]Importing from: {xlsx_path}[/dim]")
        wb = openpyxl.load_workbook(str(xlsx_path))
        ws = wb.active

        approved = rejected = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            n = len(row)
            if n >= 12:
                # New 12-column format
                name, headline, url, about, experience, relevance, hooks, message, char_count, status, shortlisted, notes = row[:12]
            elif n >= 10:
                # Legacy 10-column format
                name, headline, url, about, experience, message, char_count, status, shortlisted, notes = row[:10]
            else:
                continue

            if not url:
                continue
            profile = self._scheduler.get_by_url(str(url).strip())
            if not profile:
                continue
            if profile.status == "sent":
                continue  # never touch already-sent profiles

            val = str(shortlisted or "").strip().lower()
            if val == "yes":
                if message and str(message).strip() != (profile.message or "").strip():
                    self._scheduler.save_message(profile.id, str(message).strip()[:300])
                self._scheduler.approve_message(profile.id)
                approved += 1
            elif val == "no":
                self._scheduler.reject_message(profile.id, feedback=str(notes or ""))
                rejected += 1

        # Refresh Excel so Status and Shortlisted columns reflect import decisions
        path = self.export_to_excel()
        console.print(f"[bold cyan]Excel updated → {path}[/bold cyan]")
        return approved, rejected

    def _extract_message(self, output: str) -> str:
        lines = [
            line for line in output.strip().split("\n")
            if not line.strip().lower().startswith("character count:")
            and not line.strip().startswith("#")
            and not line.strip().startswith("---")
        ]
        return "\n".join(lines).strip()[:300]

    def _revise_message(self, message: str, issues: list[str]) -> str:
        from anthropic import Anthropic
        client = Anthropic()
        issues_str = "\n".join(f"- {i}" for i in issues)
        prompt = (
            f"Revise this LinkedIn connection message to fix the issues below.\n"
            f"Keep it under 300 characters. Return ONLY the revised message.\n\n"
            f"Original:\n{message}\n\nIssues:\n{issues_str}"
        )
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()[:300]

    # ------------------------------------------------------------------ #
    # Phase 6: Human review
    # ------------------------------------------------------------------ #

    def review_messages(self) -> int:
        drafts = self._scheduler.list_by_status("message_drafted")
        if not drafts:
            console.print("[yellow]No messages pending review.[/yellow]")
            return 0

        approved = 0
        for record in drafts:
            console.print("\n" + "=" * 60)
            console.print(Panel(
                f"[bold]{record.profile_name}[/bold]\n"
                f"[dim]{record.profile_headline}[/dim]\n"
                f"[cyan]{record.profile_url}[/cyan]",
                title="[bold cyan]Profile[/bold cyan]",
            ))

            if record.recent_posts:
                # Show first 400 chars of the hooks/analysis for context
                hooks_preview = record.recent_posts[:400].strip()
                if hooks_preview:
                    console.print(Panel(
                        hooks_preview + ("..." if len(record.recent_posts) > 400 else ""),
                        title="[dim]Profile Hooks (excerpt)[/dim]",
                    ))

            console.print(Panel(
                f"[bold white]{record.message}[/bold white]\n\n"
                f"[dim]{len(record.message or '')} / 300 characters[/dim]",
                title="[bold green]Proposed Message[/bold green]",
            ))

            action = Prompt.ask(
                "Action",
                choices=["approve", "edit", "skip", "reject"],
                default="approve",
            )

            if action == "approve":
                self._scheduler.approve_message(record.id)
                approved += 1
                console.print("[green]Approved.[/green]")
            elif action == "edit":
                new_msg = Prompt.ask("Enter revised message (300 chars max)")
                if len(new_msg) > 300:
                    console.print(f"[red]Too long ({len(new_msg)} chars). Skipping.[/red]")
                    continue
                self._scheduler.save_message(record.id, new_msg)
                self._scheduler.approve_message(record.id, feedback="human-edited")
                approved += 1
                console.print("[green]Edited and approved.[/green]")
            elif action == "reject":
                feedback = Prompt.ask("Rejection reason (optional)", default="")
                self._scheduler.reject_message(record.id, feedback)
                console.print("[yellow]Rejected.[/yellow]")
            else:
                console.print("[dim]Skipped.[/dim]")

        console.print(f"\n[bold green]Approved {approved} messages.[/bold green]")

        # Refresh Excel so Status and Shortlisted columns reflect review decisions
        path = self.export_to_excel()
        console.print(f"[bold cyan]Excel updated → {path}[/bold cyan]")
        return approved

    # ------------------------------------------------------------------ #
    # Phase 7: Send connection requests
    # ------------------------------------------------------------------ #

    def send_connections(self, limit: int = 20) -> int:
        approved = self._scheduler.list_by_status("approved")[:limit]
        if not approved:
            console.print("[yellow]No approved messages to send.[/yellow]")
            return 0

        sent = 0
        with LinkedInBrowser(headless=False) as browser:
            if not browser.is_logged_in():
                console.print("[yellow]Session expired — logging in with .env credentials...[/yellow]")
                if not browser.login(os.environ["LINKEDIN_EMAIL"], os.environ["LINKEDIN_PASSWORD"]):
                    console.print("[red]Login failed. Check LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env[/red]")
                    return 0
            for record in approved:
                console.print(f"\n  Sending to: [cyan]{record.profile_name}[/cyan]")
                result = browser.send_connection_request(record.profile_url, record.message)
                if result["success"]:
                    self._scheduler.mark_sent(record.id)
                    sent += 1
                    console.print("  [green]Sent.[/green]")
                else:
                    self._scheduler.mark_failed(record.id, result.get("error", "Unknown"))
                    console.print(f"  [red]Failed: {result.get('error')}[/red]")

        console.print(f"\n[bold green]Sent {sent} connection requests.[/bold green]")

        # Refresh Excel so Status column reflects sent/failed outcomes
        path = self.export_to_excel()
        console.print(f"[bold cyan]Excel updated → {path}[/bold cyan]")
        return sent
