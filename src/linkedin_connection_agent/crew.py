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
from pathlib import Path

import yaml
from crewai import Agent, Crew, Process, Task
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from linkedin_connection_agent.tools.browser_tool import LinkedInBrowser
from linkedin_connection_agent.tools.pdf_tool import extract_pdf_text
from linkedin_connection_agent.utils.llm_factory import LLMFactory
from linkedin_connection_agent.utils.message_validator import MessageValidator
from linkedin_connection_agent.utils.scheduler import ConnectionScheduler

console = Console()

_BASE = Path(__file__).parent.parent.parent
AGENTS_CFG = yaml.safe_load((_BASE / "config/agents.yaml").read_text(encoding="utf-8"))
TASKS_CFG = yaml.safe_load((_BASE / "config/tasks.yaml").read_text(encoding="utf-8"))
ICP_CFG = yaml.safe_load((_BASE / "config/icp_config.yaml").read_text(encoding="utf-8"))


class LinkedInConnectionCrew:
    def __init__(self):
        self._llm = LLMFactory()
        self._scheduler = ConnectionScheduler()
        self._validator = MessageValidator()

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
        search_strings = self.generate_search_strings(icp_key)
        console.print(f"\n[bold cyan]Generated {len(search_strings)} search strings.[/bold cyan]")
        for i, s in enumerate(search_strings, 1):
            segment = s.get("segment", "")
            label = f"[dim]{segment}[/dim] " if segment else ""
            console.print(f"  {i}. {label}{s['query'][:80]}...")

        new_count = 0
        with LinkedInBrowser(headless=False) as browser:
            if not browser.is_logged_in():
                console.print("[yellow]Not logged in. Run: python main.py auth[/yellow]")
                return 0
            for search in search_strings:
                console.print(f"\n[dim]Searching: {search['query'][:60]}...[/dim]")
                profiles = browser.search_people(search["query"], max_results=max_per_query)
                for p in profiles:
                    self._scheduler.save_discovered(
                        profile_url=p["url"],
                        profile_name=p["name"],
                        profile_headline=p["headline"],
                        icp_key=icp_key,
                    )
                    new_count += 1

        console.print(f"\n[bold green]Discovered {new_count} profiles.[/bold green]")
        return new_count

    # ------------------------------------------------------------------ #
    # Phase 3: Profile + post analysis
    # ------------------------------------------------------------------ #

    def analyze_profiles(self, limit: int = 10) -> int:
        discovered = self._scheduler.list_by_status("discovered")[:limit]
        if not discovered:
            console.print("[yellow]No discovered profiles to analyze.[/yellow]")
            return 0

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
        with LinkedInBrowser(headless=False) as browser:
            for record in discovered:
                console.print(f"\n  Analyzing: [cyan]{record.profile_name}[/cyan]")
                profile_data = browser.scrape_profile(record.profile_url)
                recent_posts = browser.get_recent_posts(record.profile_url, max_posts=3)

                pdf_path = str(Path("outputs/profiles/pdfs") / f"{record.id}.pdf")
                browser.download_profile_pdf(record.profile_url, pdf_path)
                pdf_text = extract_pdf_text(pdf_path) if Path(pdf_path).exists() else ""
                if pdf_text:
                    profile_data["pdf_extract"] = pdf_text[:2000]

                profile_data_str = json.dumps(profile_data, indent=2)
                recent_posts_str = json.dumps(recent_posts, indent=2)

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
                post_task = Task(
                    description=TASKS_CFG["analyze_posts_task"]["description"].format(
                        recent_posts=recent_posts_str,
                    ),
                    expected_output=TASKS_CFG["analyze_posts_task"]["expected_output"],
                    agent=post_analyzer,
                    context=[profile_task],
                )
                Crew(
                    agents=[profile_analyzer, post_analyzer],
                    tasks=[profile_task, post_task],
                    process=Process.sequential,
                    verbose=False,
                ).kickoff()

                self._scheduler.save_analyzed(
                    profile_id=record.id,
                    profile_data=profile_data_str,
                    recent_posts=recent_posts_str,
                    pdf_path=pdf_path if Path(pdf_path).exists() else "",
                )
                processed += 1

        console.print(f"\n[bold green]Analyzed {processed} profiles.[/bold green]")
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
                    profile_hooks=(record.profile_data or "")[:1500],
                    post_hook=(record.recent_posts or "")[:500],
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
        return generated

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
                try:
                    posts = json.loads(record.recent_posts)
                    if posts:
                        console.print(Panel(
                            (posts[0].get("text", "")[:300] + "..."),
                            title="[dim]Most Recent Post (excerpt)[/dim]",
                        ))
                except Exception:
                    pass

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
                console.print("[yellow]Not logged in. Run: python main.py auth[/yellow]")
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
        return sent
