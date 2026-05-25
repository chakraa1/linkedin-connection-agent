"""
LinkedIn Browser Automation — Playwright-based scraper and interaction layer.

Handles: session management, people search, profile scraping, recent posts,
PDF profile download, and connection request sending.

Rate limiting: 2-4s random delays between actions.
Daily connection limit enforced externally in ConnectionScheduler.
"""
import json
import random
import re
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
        self._context.clear_cookies()
        self._page.goto("https://www.linkedin.com/login", wait_until="load")
        # LinkedIn redirected to feed — session valid via localStorage/IndexedDB
        if "feed" in self._page.url and "login" not in self._page.url:
            self._context.storage_state(path=str(SESSION_FILE))
            return True
        self._page.wait_for_selector("#username", state="visible", timeout=180000)
        self._page.click("#username")
        self._page.type("#username", email, delay=60)
        _sleep()
        self._page.click("#password")
        self._page.type("#password", password, delay=60)
        _sleep()
        self._page.click('[data-litms-control-urn="login-submit"]')
        self._page.wait_for_load_state("load", timeout=20000)
        self._context.storage_state(path=str(SESSION_FILE))
        return "feed" in self._page.url and "login" not in self._page.url

    def is_logged_in(self) -> bool:
        self._page.goto("https://www.linkedin.com/feed/", wait_until="load")
        # Wait for final URL to settle after any redirects
        try:
            self._page.wait_for_url("**/feed/**", timeout=8000)
        except Exception:
            pass
        logged_in = "feed" in self._page.url and "login" not in self._page.url
        if not logged_in and SESSION_FILE.exists():
            SESSION_FILE.unlink()
        return logged_in

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_search_url(query: str) -> str:
        """
        Split a boolean query into LinkedIn's proper URL parameters.
        - title:"..." terms  → titleFacets (LinkedIn's native title filter)
        - remaining keywords → keywords param (supports OR/AND between phrases)
        """
        title_terms = re.findall(r'title:"([^"]+)"', query, re.IGNORECASE)

        # Remove all title:"..." fragments (and any trailing OR that follows each one)
        keyword_part = re.sub(r'title:"[^"]*"(\s*OR\s*)?', '', query, flags=re.IGNORECASE)
        # Remove empty parens left behind first, then strip leading AND/OR
        keyword_part = re.sub(r'\(\s*\)', '', keyword_part)
        keyword_part = re.sub(r'^\s*(AND|OR)\s*', '', keyword_part.strip()).strip()

        params: list[tuple[str, str]] = []
        if keyword_part:
            params.append(("keywords", keyword_part))
        if title_terms:
            params.append(("titleFacets", json.dumps(title_terms)))
        params.append(("network", '["S","O"]'))

        return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode(params)

    def search_people(self, query: str, max_results: int = 25) -> list[dict]:
        """Search LinkedIn people and return [{name, headline, url}]."""
        url = self._build_search_url(query)
        self._page.goto(url)
        _sleep()
        self._page.wait_for_load_state("load", timeout=15000)

        if "login" in self._page.url:
            raise RuntimeError("LinkedIn redirected to login during search — session expired")

        profiles: list[dict] = []
        seen_urls: set[str] = set()

        while len(profiles) < max_results:
            # Try multiple selectors — LinkedIn renames classes frequently
            cards = (
                self._page.query_selector_all(".reusable-search__result-container")
                or self._page.query_selector_all("li.artdeco-list__item")
                or self._page.query_selector_all("[data-view-name='search-entity-result-universal-template']")
            )
            if cards:
                for card in cards:
                    name_el = (
                        card.query_selector(".entity-result__title-text a")
                        or card.query_selector("span[aria-hidden='true']")
                    )
                    headline_el = (
                        card.query_selector(".entity-result__primary-subtitle")
                        or card.query_selector(".entity-result__summary")
                    )
                    link_el = card.query_selector("a[href*='/in/']")
                    if not link_el:
                        continue
                    href = (link_el.get_attribute("href") or "").split("?")[0].rstrip("/")
                    if "/in/" not in href or href in seen_urls:
                        continue
                    seen_urls.add(href)
                    name = ""
                    if name_el:
                        name = name_el.inner_text().strip()
                    elif link_el:
                        name = link_el.inner_text().strip()[:80]
                    profiles.append({
                        "name": name,
                        "headline": headline_el.inner_text().strip() if headline_el else "",
                        "url": href,
                    })
            else:
                # Fallback: collect all /in/ links on the page
                all_links = self._page.query_selector_all("a[href*='/in/']")
                for link_el in all_links:
                    href = (link_el.get_attribute("href") or "").split("?")[0].rstrip("/")
                    if "/in/" not in href or href in seen_urls:
                        continue
                    seen_urls.add(href)
                    profiles.append({
                        "name": link_el.inner_text().strip()[:80],
                        "headline": "",
                        "url": href,
                    })

            if len(profiles) >= max_results:
                break
            next_btn = self._page.query_selector('button[aria-label="Next"]')
            if not next_btn or not next_btn.is_enabled():
                break
            next_btn.click()
            _sleep()
            self._page.wait_for_load_state("load", timeout=10000)

        return profiles[:max_results]

    # ------------------------------------------------------------------ #
    # Profile scraping
    # ------------------------------------------------------------------ #

    def scrape_profile(self, profile_url: str) -> dict:
        """Scrape headline, about, experience and posts from a LinkedIn profile."""
        self._page.goto(profile_url)
        _sleep()
        self._page.wait_for_load_state("load", timeout=15000)
        self._page.evaluate("window.scrollTo(0, 0)")

        data: dict = {"url": profile_url}

        # Name
        try:
            data["name"] = self._page.query_selector("h1").inner_text().strip()
        except Exception:
            data["name"] = ""

        # Headline
        try:
            el = (
                self._page.query_selector(".text-body-medium.break-words")
                or self._page.query_selector(".pv-text-details__left-panel .text-body-medium")
                or self._page.query_selector("[data-view-name='profile-component-entity'] .text-body-medium")
            )
            data["headline"] = el.inner_text().strip() if el else ""
        except Exception:
            data["headline"] = ""

        # Scroll down gradually so lazy-loaded sections render
        for offset in [400, 800, 1200]:
            self._page.evaluate(f"window.scrollTo(0, {offset})")
            time.sleep(0.4)

        # About
        try:
            for btn_sel in [
                "#about ~ div button[aria-label*='see more']",
                "#about ~ div .inline-show-more-text__button",
                "#about ~ * button.lt-line-clamp__more",
            ]:
                btn = self._page.query_selector(btn_sel)
                if btn and btn.is_visible():
                    btn.click()
                    time.sleep(0.3)
                    break
            about_el = (
                self._page.query_selector("#about ~ div .pv-shared-text-with-see-more")
                or self._page.query_selector("#about ~ div .visually-hidden + span")
                or self._page.query_selector("section:has(#about) span[aria-hidden='true']")
            )
            data["about"] = about_el.inner_text().strip()[:1500] if about_el else ""
        except Exception:
            data["about"] = ""

        # Experience (top 3 roles)
        try:
            exp_items = (
                self._page.query_selector_all("#experience ~ div li.artdeco-list__item")
                or self._page.query_selector_all("#experience ~ div .pvs-list__item--line-separated")
            )
            data["experience"] = [
                item.inner_text().strip()[:400]
                for item in exp_items[:3]
                if item.inner_text().strip()
            ]
        except Exception:
            data["experience"] = []

        return data

    # ------------------------------------------------------------------ #
    # PDF download
    # ------------------------------------------------------------------ #

    def download_profile_pdf(self, profile_url: str, output_path: str) -> tuple[bool, str]:
        """Open profile → click ... → Save to PDF → save file.
        Returns (success, reason) so caller can log why it failed."""
        # Step 1: Open the profile and scroll to top so header buttons are visible
        self._page.goto(profile_url)
        _sleep()
        self._page.wait_for_load_state("load", timeout=15000)
        self._page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)

        try:
            # Step 2: Find the ... More button — LinkedIn uses different labels by context
            more_btn = None
            for selector in [
                "button[aria-label='More actions']",
                "button[aria-label='More member actions']",
                "button[aria-label*='More']",
                ".pvs-profile-actions button.artdeco-dropdown__trigger",
                ".pvs-profile-actions__action button",
            ]:
                el = self._page.query_selector(selector)
                if el and el.is_visible():
                    more_btn = el
                    break

            if not more_btn:
                return False, "More (...) button not found"

            more_btn.click()
            time.sleep(1.5)

            # Step 3: Find Save to PDF in the dropdown
            save_pdf = None
            for selector in [
                "[aria-label='Save to PDF']",
                "div[aria-label='Save to PDF']",
                "span[aria-label='Save to PDF']",
                "li:has-text('Save to PDF')",
                "div:has-text('Save to PDF')",
            ]:
                try:
                    el = self._page.query_selector(selector)
                    if el and el.is_visible():
                        save_pdf = el
                        break
                except Exception:
                    continue

            if not save_pdf:
                # Last resort: role-based and text-based
                try:
                    el = self._page.get_by_role("menuitem", name="Save to PDF")
                    if el.is_visible():
                        save_pdf = el
                except Exception:
                    pass
            if not save_pdf:
                try:
                    el = self._page.get_by_text("Save to PDF", exact=True)
                    if el.is_visible():
                        save_pdf = el
                except Exception:
                    pass

            if not save_pdf:
                self._page.keyboard.press("Escape")
                return False, "Save to PDF option not in dropdown"

            # Step 4: Click triggers download — must be inside the with block
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with self._page.expect_download(timeout=30000) as dl_info:
                save_pdf.click()
            dl_info.value.save_as(output_path)
            return True, ""

        except Exception as exc:
            try:
                self._page.keyboard.press("Escape")
            except Exception:
                pass
            return False, str(exc)

    # ------------------------------------------------------------------ #
    # Recent posts scraping
    # ------------------------------------------------------------------ #

    def scrape_recent_posts(self, profile_url: str, max_posts: int = 3) -> list[str]:
        """Fetch up to max_posts recent posts from the profile's activity page.

        Returns a list of post text strings (empty list if no posts found or
        not logged in).
        """
        activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
        try:
            self._page.goto(activity_url, timeout=15000)
            _sleep()
            self._page.wait_for_load_state("load", timeout=15000)
        except Exception:
            return []

        if "login" in self._page.url:
            return []

        # Lazy-load the feed
        for offset in [500, 1000]:
            self._page.evaluate(f"window.scrollTo(0, {offset})")
            time.sleep(0.4)

        posts: list[str] = []

        # LinkedIn changes class names frequently; try multiple selectors
        candidate_selectors = [
            ".feed-shared-update-v2__description span[dir='ltr']",
            ".feed-shared-text span[dir='ltr']",
            ".update-components-text span[dir='ltr']",
            ".feed-shared-update-v2 span[dir='ltr']",
            "article span[dir='ltr']",
        ]
        seen_texts: set[str] = set()

        for sel in candidate_selectors:
            if len(posts) >= max_posts:
                break
            try:
                els = self._page.query_selector_all(sel)
                for el in els:
                    if len(posts) >= max_posts:
                        break
                    try:
                        text = el.inner_text().strip()
                        # Require meaningful length and no duplicate text
                        if len(text) >= 80 and text not in seen_texts:
                            seen_texts.add(text)
                            posts.append(text[:800])
                    except Exception:
                        continue
            except Exception:
                continue

        return posts

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
        self._page.wait_for_load_state("load", timeout=15000)

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
