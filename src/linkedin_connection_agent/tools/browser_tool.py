"""
LinkedIn Browser Automation — Playwright-based scraper and interaction layer.

Handles: session management, people search, profile scraping, recent posts,
PDF profile download, and connection request sending.

Rate limiting: 2-4s random delays between actions.
Daily connection limit enforced externally in ConnectionScheduler.
"""
import json
import random
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

SESSION_FILE = Path("outputs/linkedin_session.json")
PDF_DIR = Path("outputs/profiles/pdfs")
PDF_DIR.mkdir(parents=True, exist_ok=True)

_DELAY_MIN = 2.0
_DELAY_MAX = 4.0

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _sleep():
    time.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))


class LinkedInBrowser:
    """Context manager for a Playwright LinkedIn browser session."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def __enter__(self):
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_opts: dict = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": _USER_AGENT,
            "locale": "en-US",
        }
        if SESSION_FILE.exists():
            context_opts["storage_state"] = str(SESSION_FILE)
        self._context = self._browser.new_context(**context_opts)
        self._page = self._context.new_page()
        return self

    def __exit__(self, *args):
        if self._context:
            self._context.storage_state(path=str(SESSION_FILE))
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #

    def login(self, email: str, password: str) -> bool:
        self._page.goto("https://www.linkedin.com/login")
        _sleep()
        self._page.fill("#username", email)
        _sleep()
        self._page.fill("#password", password)
        _sleep()
        self._page.click('[data-litms-control-urn="login-submit"]')
        self._page.wait_for_load_state("networkidle", timeout=15000)
        self._context.storage_state(path=str(SESSION_FILE))
        return "feed" in self._page.url

    def is_logged_in(self) -> bool:
        self._page.goto("https://www.linkedin.com/feed/")
        self._page.wait_for_load_state("networkidle", timeout=10000)
        return "feed" in self._page.url

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    def search_people(self, query: str, max_results: int = 25) -> list[dict]:
        """Search LinkedIn people and return [{name, headline, url}]."""
        encoded = urllib.parse.quote(query)
        # Target 2nd-degree connections
        url = (
            f"https://www.linkedin.com/search/results/people/"
            f"?keywords={encoded}&network=%5B%22S%22%2C%22O%22%5D"
        )
        self._page.goto(url)
        _sleep()
        self._page.wait_for_load_state("networkidle", timeout=15000)

        profiles: list[dict] = []
        seen_urls: set[str] = set()

        while len(profiles) < max_results:
            cards = self._page.query_selector_all(".reusable-search__result-container")
            for card in cards:
                name_el = card.query_selector(".entity-result__title-text a")
                headline_el = card.query_selector(".entity-result__primary-subtitle")
                if not name_el:
                    continue
                href = (name_el.get_attribute("href") or "").split("?")[0].rstrip("/")
                if "/in/" not in href or href in seen_urls:
                    continue
                seen_urls.add(href)
                profiles.append({
                    "name": name_el.inner_text().strip(),
                    "headline": headline_el.inner_text().strip() if headline_el else "",
                    "url": href,
                })

            if len(profiles) >= max_results:
                break
            next_btn = self._page.query_selector('button[aria-label="Next"]')
            if not next_btn or not next_btn.is_enabled():
                break
            next_btn.click()
            _sleep()
            self._page.wait_for_load_state("networkidle", timeout=10000)

        return profiles[:max_results]

    # ------------------------------------------------------------------ #
    # Profile scraping
    # ------------------------------------------------------------------ #

    def scrape_profile(self, profile_url: str) -> dict:
        """Extract structured data from a LinkedIn profile page."""
        self._page.goto(profile_url)
        _sleep()
        self._page.wait_for_load_state("networkidle", timeout=15000)

        data: dict = {"url": profile_url}

        try:
            data["name"] = self._page.query_selector("h1").inner_text().strip()
        except Exception:
            data["name"] = ""

        try:
            el = self._page.query_selector(".text-body-medium.break-words")
            data["headline"] = el.inner_text().strip() if el else ""
        except Exception:
            data["headline"] = ""

        try:
            about_btn = self._page.query_selector(
                "#about ~ div .inline-show-more-text__button"
            )
            if about_btn:
                about_btn.click()
                time.sleep(0.5)
            about_el = self._page.query_selector(
                "#about ~ div .pv-shared-text-with-see-more"
            )
            data["about"] = about_el.inner_text().strip() if about_el else ""
        except Exception:
            data["about"] = ""

        try:
            exp_items = self._page.query_selector_all(
                "#experience ~ div .pvs-list__item--line-separated"
            )[:3]
            data["experience"] = [
                item.inner_text().strip()[:300] for item in exp_items if item.inner_text().strip()
            ]
        except Exception:
            data["experience"] = []

        try:
            skill_els = self._page.query_selector_all(
                "#skills ~ div .pvs-list__item--line-separated"
            )[:5]
            data["skills"] = [el.inner_text().strip()[:100] for el in skill_els]
        except Exception:
            data["skills"] = []

        return data

    # ------------------------------------------------------------------ #
    # Recent posts
    # ------------------------------------------------------------------ #

    def get_recent_posts(self, profile_url: str, max_posts: int = 3) -> list[dict]:
        """Get recent posts from a profile's activity page."""
        activity_url = profile_url.rstrip("/") + "/recent-activity/shares/"
        self._page.goto(activity_url)
        _sleep()
        self._page.wait_for_load_state("networkidle", timeout=15000)

        posts: list[dict] = []
        post_items = self._page.query_selector_all(
            ".scaffold-finite-scroll__content .occludable-update"
        )[:max_posts]

        for item in post_items:
            try:
                see_more = item.query_selector(
                    ".feed-shared-inline-show-more-text__see-more-less-toggle"
                )
                if see_more:
                    see_more.click()
                    time.sleep(0.5)
                text_el = item.query_selector(".feed-shared-update-v2__description")
                text = text_el.inner_text().strip() if text_el else ""
                if text:
                    posts.append({"text": text[:1000]})
            except Exception:
                continue

        return posts

    # ------------------------------------------------------------------ #
    # PDF download
    # ------------------------------------------------------------------ #

    def download_profile_pdf(self, profile_url: str, output_path: str) -> bool:
        """Download the LinkedIn profile as a PDF via More > Save to PDF."""
        self._page.goto(profile_url)
        _sleep()
        self._page.wait_for_load_state("networkidle", timeout=15000)

        try:
            more_btn = self._page.query_selector(
                "button[aria-label*='More actions']"
            ) or self._page.query_selector(
                ".pvs-profile-actions__action[aria-label*='More']"
            )
            if not more_btn:
                return False
            more_btn.click()
            _sleep()

            save_pdf = self._page.query_selector("text=Save to PDF")
            if not save_pdf:
                return False

            with self._page.expect_download() as dl_info:
                save_pdf.click()
                dl = dl_info.value
                dl.save_as(output_path)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Send connection request
    # ------------------------------------------------------------------ #

    def send_connection_request(self, profile_url: str, message: str) -> dict:
        """
        Send a connection request with a personalized note.
        Returns {"success": bool, "error": str | None}
        """
        if len(message) > 300:
            return {
                "success": False,
                "error": f"Message exceeds 300 chars ({len(message)})",
            }

        self._page.goto(profile_url)
        _sleep()
        self._page.wait_for_load_state("networkidle", timeout=15000)

        try:
            connect_btn = self._page.query_selector(
                "button[aria-label*='Connect']"
            ) or self._page.query_selector(
                ".pvs-profile-actions button:has-text('Connect')"
            )

            if not connect_btn:
                more_btn = self._page.query_selector("button[aria-label*='More actions']")
                if more_btn:
                    more_btn.click()
                    _sleep()
                    connect_btn = self._page.query_selector("text=Connect")

            if not connect_btn:
                return {
                    "success": False,
                    "error": "Connect button not found — may already be connected or pending",
                }

            connect_btn.click()
            _sleep()

            add_note_btn = self._page.query_selector(
                "button[aria-label*='Add a note']"
            ) or self._page.query_selector("text=Add a note")

            if add_note_btn:
                add_note_btn.click()
                _sleep()
                note_field = self._page.query_selector(
                    "#custom-message"
                ) or self._page.query_selector("textarea[name='message']")
                if note_field:
                    note_field.fill(message)
                    _sleep()

            send_btn = self._page.query_selector(
                "button[aria-label*='Send now']"
            ) or self._page.query_selector("button:has-text('Send')")

            if send_btn:
                send_btn.click()
                _sleep()
                return {"success": True, "error": None}

            return {"success": False, "error": "Send button not found"}

        except Exception as exc:
            return {"success": False, "error": str(exc)}
