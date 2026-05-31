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


def _parse_card_text(raw: str) -> tuple[str, str]:
    """Extract (name, headline) from LinkedIn search card link text.

    Card text format: "Name • 2nd\\n\\nHeadline\\n\\nLocation"
    Degree indicator (• 2nd / • 3rd) is stripped from name.
    Returns ("", "") if the text can't be parsed meaningfully.
    """
    if not raw:
        return "", ""
    # Split on bullet (degree indicator) first
    if "•" in raw:
        name_part, rest = raw.split("•", 1)
        name = name_part.strip()
        # Rest is "2nd\n\nHeadline\n\nLocation" — skip the degree token
        segments = [s.strip() for s in rest.split("\n\n") if s.strip()]
        # segments[0] = "2nd" or "3rd", segments[1] = headline
        headline = segments[1] if len(segments) >= 2 else (segments[0] if segments else "")
        # Sanity: degree tokens are short; if headline looks like a degree, skip
        if headline in ("1st", "2nd", "3rd", "Connection", "Follow"):
            headline = ""
    else:
        # No bullet — just the name, no degree
        segments = [s.strip() for s in raw.split("\n\n") if s.strip()]
        name = segments[0][:80] if segments else raw[:80]
        headline = segments[1] if len(segments) >= 2 else ""
    return name[:100], headline[:200]


_SKIP_LINES = {
    "Message", "Follow", "Connect", "More", "Share", "Unfollow",
    "1st", "2nd", "3rd", "Contact info", "Open to", "Open for",
    "Saved", "Pending", "Withdraw", "Report / Block",
}


def _extract_headline_from_body(body_text: str, name: str) -> str:
    """Extract the LinkedIn headline from page body text.

    LinkedIn's body text has a consistent structure near the top:
        [Name]
        [Headline]
        [Location] · [Contact info]
        Message / Follow / Connect

    We find the first occurrence of the person's first name (or full name),
    then scan the next few non-empty lines for the headline.
    """
    if not body_text or not name:
        return ""

    first_name = name.split()[0] if name else ""
    lines = [ln.strip() for ln in body_text.split("\n") if ln.strip()]

    # Locate name in body text
    name_idx = -1
    for i, line in enumerate(lines):
        if name in line or (first_name and first_name in line and len(line) < 120):
            name_idx = i
            break

    if name_idx < 0:
        return ""

    # Scan the next 6 lines for a plausible headline
    for line in lines[name_idx + 1: name_idx + 7]:
        if (line not in _SKIP_LINES
                and len(line) > 8
                and not line.startswith("·")
                and not line.startswith("0 ")
                and "notification" not in line.lower()
                and "Skip to" not in line):
            return line[:200]

    return ""


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
        - title:"..." terms in positive context → titleFacets (LinkedIn's native include filter)
        - remaining keywords including NOT clauses → keywords param (supports AND/OR/NOT)

        Title terms inside NOT(...) blocks are deliberately NOT added to titleFacets
        (titleFacets only supports inclusion). They stay in the keyword_part so LinkedIn
        can apply them as keyword-level NOT exclusions.
        """
        # Step 1: Extract all NOT(...) blocks to separate them from the positive query
        not_blocks = re.findall(r'\bNOT\s*\([^)]+\)', query, re.IGNORECASE)
        not_inline = re.findall(r'\bNOT\s+"[^"]+"', query, re.IGNORECASE)

        # Step 2: Positive query = original minus all NOT blocks
        positive_query = re.sub(r'\bNOT\s*\([^)]+\)', '', query, flags=re.IGNORECASE)
        positive_query = re.sub(r'\bNOT\s+"[^"]+"', '', positive_query, flags=re.IGNORECASE)

        # Step 3: Extract title facets only from the positive query (never from NOT context)
        title_terms = re.findall(r'title:"([^"]+)"', positive_query, re.IGNORECASE)

        # Step 4: Build keyword_part = positive query minus title terms + NOT blocks appended
        kw_positive = re.sub(r'title:"[^"]*"(\s*OR\s*)?', '', positive_query, flags=re.IGNORECASE)
        kw_positive = re.sub(r'\(\s*\)', '', kw_positive)
        kw_positive = re.sub(r'^\s*(AND|OR)\s*', '', kw_positive.strip()).strip()
        not_suffix = " ".join(not_blocks + not_inline).strip()
        keyword_part = (kw_positive + " " + not_suffix).strip()

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
                        or card.query_selector(".entity-result__content span[aria-hidden='true']")
                        or card.query_selector("span.entity-result__title-text ~ div span")
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
                # Fallback: collect all /in/ links on the page and parse name+headline
                # from the link text (format: "Name • Nth\n\nHeadline\n\nLocation")
                all_links = self._page.query_selector_all("a[href*='/in/']")
                for link_el in all_links:
                    href = (link_el.get_attribute("href") or "").split("?")[0].rstrip("/")
                    if "/in/" not in href or href in seen_urls:
                        continue
                    seen_urls.add(href)
                    raw = link_el.inner_text().strip()
                    # Parse "Name • 2nd\n\nHeadline\n\nLocation" structure
                    name, headline = _parse_card_text(raw)
                    profiles.append({"name": name, "headline": headline, "url": href})

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

        # Name — try h1, fall back to page title ("Name | LinkedIn")
        try:
            h1 = self._page.query_selector("h1")
            data["name"] = h1.inner_text().strip() if h1 else ""
        except Exception:
            data["name"] = ""
        if not data["name"]:
            try:
                title = self._page.title()
                data["name"] = title.replace("| LinkedIn", "").replace("| LinkedIn", "").strip().rstrip("|").strip()
            except Exception:
                pass

        # Headline — LinkedIn now uses hashed CSS classes that change with each deploy.
        # Parse from page body text: headline appears right after the person's name.
        data["headline"] = ""
        try:
            body_text = self._page.evaluate("() => document.body.innerText") or ""
            data["headline"] = _extract_headline_from_body(body_text, data.get("name", ""))
        except Exception:
            pass

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
