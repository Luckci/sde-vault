#!/usr/bin/env python3
"""
SDE Moodle Downloader
=====================
Downloads course content from learn.sde.dk and builds an Obsidian vault
with proper folder hierarchy, Markdown notes, downloaded files, and [[wikilinks]].

Usage:
    python sde_downloader.py --subject "Fysik A"
    python sde_downloader.py --subject "Fysik A" --topic "Energi"
    python sde_downloader.py --subject "Matematik" --output "C:/MyVault"
    python sde_downloader.py --list   # List all available courses

Credentials:
    Create a .env file next to this script with:
        EMAIL=madsb@otg.dk
        PASSWORD=your_password
"""

import sys
import os
import re
import time
import json
import hashlib
import argparse
import requests
import io
import traceback
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote

import pyautogui

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

try:
    from markdownify import markdownify as md_convert
    HAS_MARKDOWNIFY = True
except ImportError:
    HAS_MARKDOWNIFY = False
    try:
        import html2text as h2t
    except ImportError:
        h2t = None

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# FIX: line_buffering=True so print() flushes immediately instead of buffering until exit
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

BASE_URL = "https://learn.sde.dk"

# ─────────────────────────────────────────────
# BROWSER SETUP
# ─────────────────────────────────────────────

def setup_driver(headless: bool = False) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

def is_logged_in(driver: webdriver.Chrome) -> bool:
    return BASE_URL in driver.current_url and (
        "/my" in driver.current_url
        or "Betjeningspanel" in driver.title
        or "Dashboard" in driver.title
    )


def login(driver: webdriver.Chrome, wait: WebDriverWait, email: str, password: str) -> bool:
    """
    Proven login chain for learn.sde.dk (matches homework.py which works in production):

      1. learn.sde.dk            → click button id="login"  (Microsoft Login)
      2. login.microsoftonline.com → enter email in id="i0116" → click Next
      3. adfs.sde.dk (HRD page)  → click span.largeTextNoWrap "Active Directory"
      4. adfs.sde.dk (ADFS form) → enter password in id="passwordInput" → submit
      5. "Stay signed in?" prompt → click No  (id="idBtn_Back")
    """
    print("[login] Navigating to learn.sde.dk …")
    driver.get(BASE_URL)
    time.sleep(2)

    if is_logged_in(driver):
        print("[login] Already logged in ✓")
        return True

    try:
        # Step 1 ── Microsoft Login button
        print("[login] Step 1 – Microsoft Login button …")
        wait.until(EC.element_to_be_clickable((By.ID, "login"))).click()

        # Step 2 ── Microsoft Online: email field (id="i0116") + Next
        print("[login] Step 2 – entering email …")
        wait.until(EC.presence_of_element_located((By.ID, "i0116"))).send_keys(email)
        time.sleep(1)
        wait.until(EC.element_to_be_clickable((By.ID, "idSIButton9"))).click()

        # Step 3 ── ADFS Home Realm Discovery: click "Active Directory"
        print("[login] Step 3 – clicking Active Directory …")
        wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//span[@class='largeTextNoWrap indentNonCollapsible' and text()='Active Directory']"
        ))).click()

        # Step 4 ── ADFS credentials form: password only (username pre-filled by ADFS)
        print("[login] Step 4 – entering password …")
        wait.until(EC.presence_of_element_located((By.ID, "passwordInput"))).send_keys(password)
        wait.until(EC.element_to_be_clickable((By.ID, "submitButton"))).click()

        # Step 5 ── "Stay signed in?" → No
        try:
            wait.until(EC.element_to_be_clickable((By.ID, "idBtn_Back"))).click()
        except Exception:
            pass  # prompt doesn't always appear

        print("[login] Logged in ✓")
        time.sleep(2)

    except Exception as e:
        print(f"[login] Standard login failed at: {driver.current_url}")
        print(f"[login] Exception: {e}")
        print("[login] Checking for native authentication popup …")
        time.sleep(0.5)
        try:
            # The ADFS popup is a native Windows dialog — Selenium can't reach it.
            # Use PyAutoGUI to type directly into whichever field has focus.
            # NAME env var holds the short login (e.g. "madsb"), not the full email.
            username = os.getenv("NAME") or email
            pyautogui.write(username)
            pyautogui.press("tab")
            pyautogui.write(password)
            pyautogui.press("enter")
            print("[login] Popup login submitted via PyAutoGUI.")
            time.sleep(3)
        except Exception as pe:
            print(f"[login] PyAutoGUI popup fallback also failed: {pe}")
            return False

    if is_logged_in(driver):
        print("[login] Login confirmed ✓")
        return True

    # Fallback: nudge to dashboard and check again
    driver.get(f"{BASE_URL}/my/")
    time.sleep(2)
    return is_logged_in(driver)


# ─────────────────────────────────────────────
# COURSE DISCOVERY
# ─────────────────────────────────────────────

def get_course_list(driver: webdriver.Chrome, wait: WebDriverWait) -> dict[str, int]:
    """Returns {course_name: course_id} for all enrolled courses."""
    print("[courses] Fetching course list from dashboard …")
    driver.get(f"{BASE_URL}/my/")
    time.sleep(3)

    # Wait for cards to load
    try:
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "a[href*='/course/view.php']")
        ))
    except Exception:
        pass

    courses: dict[str, int] = {}
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/course/view.php']")
    for link in links:
        name = link.text.strip()
        href = link.get_attribute("href") or ""
        # Skip blank / "starred" labels
        if not name or "stjerne" in name.lower() or "markeret" in name.lower():
            continue
        m = re.search(r"id=(\d+)", href)
        if m and name not in courses:
            courses[name] = int(m.group(1))

    print(f"[courses] Found {len(courses)} courses")
    return courses


def find_course(courses: dict[str, int], query: str) -> tuple[str, int]:
    """Fuzzy match a subject query to an enrolled course."""
    q = query.lower().strip()

    # Exact
    for name, cid in courses.items():
        if name.lower() == q:
            return name, cid

    # Subject starts with query (e.g. "Fysik" matches "Fysik A")
    for name, cid in courses.items():
        if name.lower().startswith(q):
            return name, cid

    # Query inside name or name inside query
    for name, cid in courses.items():
        if q in name.lower() or name.lower() in q:
            return name, cid

    # First word match
    for name, cid in courses.items():
        first_word = name.lower().split()[0]
        if first_word == q.split()[0]:
            return name, cid

    return None, None


# ─────────────────────────────────────────────
# SECTION / TOPIC DISCOVERY
# ─────────────────────────────────────────────

def get_sections(driver: webdriver.Chrome, wait: WebDriverWait, course_id: int) -> list[dict]:
    """Returns list of {name, id, url} dicts for every section in the course."""
    print(f"[sections] Fetching sections for course {course_id} …")
    driver.get(f"{BASE_URL}/course/view.php?id={course_id}")
    time.sleep(2)

    sections = []
    seen: set[str] = set()

    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/course/section.php']")
    for link in links:
        href = link.get_attribute("href") or ""
        name = link.text.strip()
        if not name or href in seen:
            continue
        seen.add(href)
        m = re.search(r"id=(\d+)", href)
        if m:
            sections.append({"name": name, "id": int(m.group(1)), "url": href})

    print(f"[sections] Found {len(sections)} sections: {[s['name'] for s in sections]}")
    return sections


# ─────────────────────────────────────────────
# SECTION CONTENT SCRAPING
# ─────────────────────────────────────────────

ACTIVITY_TYPES = {
    "assign":      ("📝", "Opgave"),
    "resource":    ("📄", "Fil"),
    "page":        ("📃", "Side"),
    "url":         ("🔗", "Link"),
    "folder":      ("📁", "Mappe"),
    "praxisgroup": ("🔬", "Forsøg"),
    "quiz":        ("❓", "Quiz"),
    "forum":       ("💬", "Forum"),
}

# Danish type labels used to strip suffix from link text
_TYPE_LABELS = {label for _, label in ACTIVITY_TYPES.values()}


def _extract_activity_name(link, mod_type: str) -> str:
    """
    Multi-strategy name extraction for a Moodle activity anchor element.

    Moodle's pxbase theme sometimes renders the activity name OUTSIDE the <a>
    tag (in a sibling span.instancename), or in a CSS-hidden span. We try several
    fallbacks before giving up.
    """
    text = ""

    # Strategy 1: span.instancename INSIDE the link (standard Moodle)
    if not text:
        try:
            iname = link.find_element(By.CSS_SELECTOR, "span.instancename")
            text = iname.text.strip()
        except Exception:
            pass

    # Strategy 2: direct link text (works when name IS inside the <a>)
    if not text:
        text = link.text.strip()

    # Strategy 3: aria-label / title attribute on the link
    if not text:
        for attr in ("aria-label", "title"):
            val = link.get_attribute(attr) or ""
            val = re.sub(r'^Vælg aktivitet\s+', '', val).strip()
            if val:
                text = val
                break

    # Strategy 4: climb to ancestor <li class="activity"> and read instancename there
    if not text:
        try:
            li = link.find_element(
                By.XPATH,
                "ancestor::li[contains(@class,'activity')][1]"
            )
            iname = li.find_element(By.CSS_SELECTOR, "span.instancename")
            text = iname.text.strip()
        except Exception:
            pass

    # Strip "Vælg aktivitet " accessibility prefix if it slipped through
    text = re.sub(r'^Vælg aktivitet\s+', '', text).strip()

    # Strip trailing Danish type label that comes from span.accesshide
    # e.g. "Mekanik-Opgaver Fil" → "Mekanik-Opgaver"
    _, label = ACTIVITY_TYPES.get(mod_type, ("", ""))
    if label and text.endswith(label):
        text = text[: -len(label)].strip()
    # Also strip any other known type labels just in case
    for lbl in _TYPE_LABELS:
        if text.endswith(f" {lbl}"):
            text = text[: -(len(lbl) + 1)].strip()
            break

    return text or "Unavngivet aktivitet"


def get_section_content(driver: webdriver.Chrome, wait: WebDriverWait, section: dict) -> dict:
    """
    Scrape a section page.  Returns:
    {
      name, url,
      html_content,          # raw HTML of main body
      activities: [          # all mod/* links found
        {name, url, type, local_path, downloaded}
      ]
    }
    """
    print(f"  [section] Scraping: {section['name']}")
    driver.get(section["url"])
    time.sleep(2)

    result = {
        "name":         section["name"],
        "url":          section["url"],
        "html_content": "",
        "activities":   [],
    }

    # ── Grab main HTML content ─────────────────────────────────────────────
    for sel in ["#region-main", "main", ".course-description-item", "body"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            result["html_content"] = els[0].get_attribute("innerHTML") or ""
            break

    # ── Scope activity search to #region-main to avoid sidebar/nav duplicates ──
    # FIX: previously we searched the ENTIRE page, which meant icon-only navigation
    # links (appearing before #region-main in DOM order) got added to seen_hrefs
    # first, blocking the text-containing links from being processed.
    main_region = None
    for sel in ("#region-main", "#page-content", "main", "[role='main']"):
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            main_region = els[0]
            break
    if main_region is None:
        main_region = driver  # fallback to full page

    mod_selectors = ", ".join(
        f"a[href*='/mod/{mod}/']"
        for mod in ACTIVITY_TYPES
    )
    activity_links = main_region.find_elements(By.CSS_SELECTOR, mod_selectors)

    seen_hrefs: set[str] = set()
    for link in activity_links:
        href = link.get_attribute("href") or ""
        if not href or href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        mod_type = "resource"
        for mod in ACTIVITY_TYPES:
            if f"/mod/{mod}/" in href:
                mod_type = mod
                break

        name = _extract_activity_name(link, mod_type)

        result["activities"].append({
            "name":       name,
            "url":        href,
            "type":       mod_type,
            "local_path": None,
            "downloaded": False,
        })

    print(f"    → {len(result['activities'])} activities found")
    return result


# ─────────────────────────────────────────────
# FILE DOWNLOADING
# ─────────────────────────────────────────────

def get_resource_file_url(driver: webdriver.Chrome, activity_url: str) -> str | None:
    """
    Navigate to a mod/resource page and extract the direct pluginfile.php URL.
    Many resources auto-redirect; others show a link on the page.
    """
    driver.get(activity_url)
    time.sleep(1)

    # Case 1: direct redirect to pluginfile
    if "pluginfile.php" in driver.current_url:
        return driver.current_url

    # Case 2: link on page
    els = driver.find_elements(By.CSS_SELECTOR, "a[href*='pluginfile.php']")
    if els:
        return els[0].get_attribute("href")

    # Case 3: iframe embed (e.g. PDF viewer)
    iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='pluginfile.php']")
    if iframes:
        return iframes[0].get_attribute("src")

    return None


def get_assign_feedback(
    driver: webdriver.Chrome, activity_url: str
) -> tuple[str, str, list[tuple[str, str]]]:
    """
    Navigate to a submitted Moodle assignment and scrape teacher feedback.

    Returns:
        (grade_str, feedback_markdown, submission_files: [(name, url), ...])

    All fields are empty/[] when nothing is found (graceful no-op for ungraded work).
    """
    driver.get(activity_url)
    time.sleep(1.5)

    grade    = ""
    feedback = ""
    sub_files: list[tuple[str, str]] = []

    # ── Grade ──────────────────────────────────────────────────────────────
    for sel in [
        "td.submissionstatussubmission .grade",
        ".gradingform_rubric_grade",
        "td.cell.c1",
        ".grade",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            txt = els[0].text.strip()
            # Only keep if it looks like a grade value (has digit or /)
            if txt and any(c.isdigit() or c in "/-%" for c in txt):
                grade = txt
                break

    # ── Teacher feedback / comment ─────────────────────────────────────────
    for sel in [
        ".feedback .text_to_html",
        ".feedback",
        ".gradingform_rubric",
        ".assignfeedback_comments",
        ".comment",
    ]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            html = els[0].get_attribute("innerHTML") or ""
            if html.strip():
                feedback = html_to_markdown(clean_section_html(html)).strip()
                if feedback:
                    break

    # ── Student submission files ───────────────────────────────────────────
    seen_hrefs: set[str] = set()
    for link in driver.find_elements(
        By.CSS_SELECTOR,
        "a[href*='pluginfile.php'], a[href*='assignsubmission']",
    ):
        href = link.get_attribute("href") or ""
        name = link.text.strip()
        # Skip teacher intro attachments; keep submission files
        if href and name and href not in seen_hrefs and "introattachment" not in href:
            seen_hrefs.add(href)
            sub_files.append((name, href))

    return grade, feedback, sub_files


def get_assign_content(driver: webdriver.Chrome, activity_url: str) -> tuple[str, list[str], str]:
    """
    Navigate to an assignment page and return (description_html, [pluginfile_url, ...], deadline_str).
    Collects the intro description, any attached files, and the due date (Tidsfrist).
    """
    driver.get(activity_url)
    time.sleep(1.5)

    description_html = ""
    file_urls: list[str] = []
    deadline = ""

    # Description — try several selectors used by different Moodle themes
    for sel in [".activity-description", ".box.generalbox", "#intro", ".assignintro"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            description_html = els[0].get_attribute("innerHTML") or ""
            break

    # Attached files — collect all pluginfile.php hrefs on the page
    seen: set[str] = set()
    for link in driver.find_elements(By.CSS_SELECTOR, "a[href*='pluginfile.php']"):
        href = link.get_attribute("href") or ""
        if href and href not in seen:
            seen.add(href)
            file_urls.append(href)

    # Due date — look for "Tidsfrist" in the submission info table
    try:
        for row in driver.find_elements(By.CSS_SELECTOR, "tr"):
            try:
                cells = row.find_elements(By.CSS_SELECTOR, "td, th")
                if len(cells) >= 2:
                    label = cells[0].text.strip().lower()
                    if "tidsfrist" in label:
                        date_text = cells[-1].text.strip()
                        if date_text and date_text.lower() not in ("ingen", "-", ""):
                            deadline = date_text
                            break
            except Exception:
                continue
    except Exception:
        pass

    return description_html, file_urls, deadline


def _scrape_quiz_questions(driver: webdriver.Chrome) -> list[dict]:
    """Scrape questions from an open quiz review page."""
    questions: list[dict] = []
    if not HAS_BS4:
        return questions

    soup = BeautifulSoup(driver.page_source, "html.parser")

    for q_div in soup.select("div.que"):
        q: dict = {"text": "", "type": "", "options": [], "answer": ""}

        classes = q_div.get("class", [])
        for cls in classes:
            if cls not in ("que", "clearfix"):
                q["type"] = cls
                break

        q_text_el = q_div.select_one(".qtext")
        if q_text_el:
            q["text"] = q_text_el.get_text(separator=" ", strip=True)

        for answer_el in q_div.select(".answer div"):
            label = answer_el.get_text(separator=" ", strip=True)
            cls_list = " ".join(answer_el.get("class", []))
            is_correct = "correct" in cls_list or bool(answer_el.select(".correct, .gradecorrect"))
            if label:
                q["options"].append({"text": label, "is_correct": is_correct})

        for sel in [".rightanswer", ".generalfeedback"]:
            el = q_div.select_one(sel)
            if el:
                q["answer"] = el.get_text(separator=" ", strip=True)
                break

        if q["text"]:
            questions.append(q)

    return questions


def get_quiz_content(driver: webdriver.Chrome, activity_url: str) -> tuple[str, list[dict]]:
    """
    Navigate to a quiz page and extract available content.
    Only reviews existing completed attempts — never starts a new one.
    Returns (intro_html, questions).
    """
    driver.get(activity_url)
    time.sleep(1.5)

    intro_html = ""
    questions: list[dict] = []

    for sel in [".activity-description", ".box.generalbox", "#intro"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            intro_html = els[0].get_attribute("innerHTML") or ""
            break

    review_links = driver.find_elements(
        By.CSS_SELECTOR,
        ".quizattemptsummary a[href*='review.php'], a[href*='review.php']",
    )
    if review_links:
        review_url = review_links[-1].get_attribute("href")
        print(f"    [quiz] Reviewing existing attempt …")
        driver.get(review_url)
        time.sleep(2)
        questions = _scrape_quiz_questions(driver)
        print(f"    [quiz] Found {len(questions)} question(s)")
    else:
        print(f"    [quiz] No completed attempt — saving description only")

    return intro_html, questions


def get_page_content(driver: webdriver.Chrome, activity_url: str) -> str:
    """Navigate to a Moodle page activity and return the main content HTML."""
    driver.get(activity_url)
    time.sleep(1.5)

    for sel in [".activity-description", "#region-main .box.generalbox", "#region-main"]:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            return els[0].get_attribute("innerHTML") or ""
    return ""


def download_file(session: requests.Session, url: str, dest: Path) -> bool:
    """Download url → dest using an authenticated requests session."""
    try:
        r = session.get(url, stream=True, timeout=60, allow_redirects=True)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"    [!] Download failed ({dest.name}): {e}")
        return False


def extract_pdf_text(pdf_path: Path, max_chars: int = 6000) -> str:
    """
    Extract plain text from a PDF file using pypdf.
    Returns up to max_chars characters, or empty string if extraction fails.
    """
    if not HAS_PYPDF:
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
            if sum(len(p) for p in parts) >= max_chars:
                break
        combined = "\n".join(parts)
        return combined[:max_chars].strip()
    except Exception as e:
        print(f"    [pdf] Could not extract text from {pdf_path.name}: {e}")
        return ""


def _make_progress(iterable, desc: str, total: int | None = None):
    """Wrap iterable with tqdm if available, otherwise return as-is."""
    if HAS_TQDM:
        return tqdm(iterable, desc=desc, total=total, unit="item", ncols=80)
    return iterable


def _call_ai(prompt: str, max_tokens: int = 1500, provider: str = "claude", model: str | None = None) -> str | None:
    """
    Shared helper: call claude-haiku with a prompt string.
    Returns the response text, or None if the API key is missing / call fails.
    """
    provider = provider.lower()

    # ── Claude (Anthropic) ──────────────────────────────────────────────────
    if provider == "claude":
        try:
            import anthropic
        except ImportError:
            print("  [!] Install anthropic:  pip install anthropic")
            return None
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("  [!] Add ANTHROPIC_API_KEY to .env to enable Claude.")
            return None
        _model = model or "claude-haiku-4-5"
        try:
            client  = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model=_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            print(f"  [!] Claude API call failed: {e}")
            return None

    # ── OpenAI (ChatGPT) ────────────────────────────────────────────────────
    elif provider == "openai":
        try:
            from openai import OpenAI
        except ImportError:
            print("  [!] Install openai:  pip install openai")
            return None
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("  [!] Add OPENAI_API_KEY to .env to enable ChatGPT.")
            return None
        _model = model or "gpt-4o-mini"
        try:
            client = OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"  [!] OpenAI API call failed: {e}")
            return None

    # ── Gemini (Google) ─────────────────────────────────────────────────────
    elif provider == "gemini":
        try:
            from google import genai
        except ImportError:
            print("  [!] Install google-genai:  pip install google-genai")
            return None
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("  [!] Add GEMINI_API_KEY to .env to enable Gemini.")
            return None
        _model = model or "gemini-2.0-flash"
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=_model,
                contents=prompt,
                config={"max_output_tokens": max_tokens},
            )
            return response.text
        except Exception as e:
            print(f"  [!] Gemini API call failed: {e}")
            return None

    else:
        print(f"  [!] Unknown AI provider '{provider}'. Choose: claude, openai, gemini")
        return None


def generate_ai_notes(content: str, section_name: str, course_name: str, provider: str = "claude", model: str | None = None) -> str:
    """
    Generate structured, exam-focused study notes for a topic section.
    Returns a Markdown string ready to embed directly in the topic note.
    """
    trimmed = content[:8000] if len(content) > 8000 else content
    prompt  = (
        f"Du er en eksamensforberedelsesassistent for en gymnasieelev.\n\n"
        f"Kursus: {course_name}\nEmne: {section_name}\n\n"
        f"Generer kompakte, eksamensfokuserede studienoter ud fra nedenstående indhold.\n"
        f"Brug PRÆCIS denne struktur — ingen andre overskrifter:\n\n"
        f"## 📌 Nøglebegreber\n"
        f"(3–8 bullet points: begreb — kort definition)\n\n"
        f"## 📐 Formler & definitioner\n"
        f"(tabel eller bullets med formler, enheder og anvendelse; udelad hvis emnet ikke har formler)\n\n"
        f"## ⚠️ Typiske eksamensfælder\n"
        f"(2–5 konkrete fejl studerende laver til eksamen i dette emne)\n\n"
        f"## ✅ Tjekliste\n"
        f"(6–10 checkboxes: '- [ ] Kan jeg forklare/beregne/anvende X?')\n\n"
        f"Skriv på dansk. Vær præcis og eksamensorienteret.\n\n"
        f"Indhold:\n{trimmed}"
    )
    result = _call_ai(prompt, max_tokens=1800, provider=provider, model=model)
    if result is None:
        return (
            "## 🤖 AI Studienoter\n\n"
            "*AI-noter ikke tilgængelige — tjek din API-nøgle i `.env`.*"
        )
    return result


def generate_flashcards(content: str, section_name: str, course_name: str, provider: str = "claude", model: str | None = None) -> str:
    """
    Generate Obsidian Spaced Repetition flashcards for a topic section.
    Returns a Markdown string in SR-plugin format.
    """
    trimmed = content[:8000] if len(content) > 8000 else content
    prompt  = (
        f"Du er en flashcard-generator til gymnasieelever der forbereder sig til eksamen.\n\n"
        f"Kursus: {course_name}\nEmne: {section_name}\n\n"
        f"Generer 10–15 flashcards ud fra nedenstående indhold.\n"
        f"Brug PRÆCIS dette format for hvert kort (Obsidian Spaced Repetition):\n\n"
        f"## [Kort spørgsmål her] #flashcard\n\n"
        f"[Præcist, kortfattet svar — max 3 linjer]\n\n"
        f"---\n\n"
        f"Dæk: definitioner, formler, sammenhænge og typiske eksamensspørgsmål.\n"
        f"Skriv på dansk.\n\n"
        f"Indhold:\n{trimmed}"
    )
    result = _call_ai(prompt, max_tokens=2500, provider=provider, model=model)
    if result is None:
        return (
            f"# {section_name} — Flashcards\n\n"
            "*Flashcards ikke tilgængelige — tjek din API-nøgle i `.env`.*"
        )
    header = (
        f"---\n"
        f"subject: {course_name}\n"
        f'topic: "{section_name}"\n'
        f"type: flashcards\n"
        f"---\n\n"
        f"# {section_name} — Flashcards\n\n"
        f"> [[{section_name}|← {section_name}]] · Brug med "
        f"[Obsidian Spaced Repetition](https://github.com/st3v3nmw/obsidian-spaced-repetition)\n\n"
        f"---\n\n"
    )
    return header + result


def generate_practice_exam(content: str, section_name: str, course_name: str, provider: str = "claude", model: str | None = None) -> str:
    """
    Generate a practice exam for a topic section using AI.
    Returns a Markdown string with questions and collapsible model answers.
    """
    trimmed = content[:8000] if len(content) > 8000 else content
    prompt = (
        f"Du er eksamensopgave-generator for gymnasieniveau.\n\n"
        f"Kursus: {course_name}\nEmne: {section_name}\n\n"
        f"Generer en prøveeksamen med 5-8 spørgsmål baseret på nedenstående indhold.\n"
        f"Brug PRÆCIS dette format for hvert spørgsmål:\n\n"
        f"### Spørgsmål N\n\n"
        f"[Spørgsmålstekst her]\n\n"
        f"> 💡 Hint: [kort hint — max 1 linje]\n\n"
        f"<details><summary>Modelsvar</summary>\n\n"
        f"[Detaljeret svar her]\n\n"
        f"</details>\n\n"
        f"---\n\n"
        f"Inkluder en blanding af:\n"
        f"- Faktaspørgsmål ('Definer…', 'Forklar…', 'Hvad er…')\n"
        f"- Regneopgaver ('Beregn…', 'Find…') hvis emnet har formler\n"
        f"- Analysespørgsmål ('Sammenlign…', 'Vurder…', 'Diskuter…')\n\n"
        f"Skriv på dansk. Brug gymnasieniveau.\n\n"
        f"Indhold:\n{trimmed}"
    )
    result = _call_ai(prompt, max_tokens=3000, provider=provider, model=model)
    if result is None:
        return (
            f"# {section_name} — Prøveeksamen\n\n"
            "*Prøveeksamen ikke tilgængelig — tjek din API-nøgle i `.env`.*"
        )
    header = (
        f"---\n"
        f"subject: {course_name}\n"
        f'topic: "{section_name}"\n'
        f"type: practice-exam\n"
        f"---\n\n"
        f"# {section_name} — Prøveeksamen\n\n"
        f"> [[{section_name}|← {section_name}]] · *AI-genereret prøveeksamen*\n\n"
        f"---\n\n"
        f"## 📝 Spørgsmål\n\n"
    )
    return header + result


def get_requests_session(driver: webdriver.Chrome) -> requests.Session:
    """Build a requests session seeded with Selenium's current cookies."""
    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])
    ua = driver.execute_script("return navigator.userAgent")
    session.headers.update({"User-Agent": ua, "Referer": BASE_URL})
    return session


def _safe_image_name(fname: str) -> str:
    """
    Sanitize an image filename so it's safe in Obsidian wikilinks.
    Spaces and parentheses in filenames break the [[wikilink]] and ![[img]] syntax.
    e.g. "image (1).png" → "image_1.png"
    """
    name = Path(fname).stem
    ext  = Path(fname).suffix
    # Replace anything that's not alphanumeric, underscore, hyphen, or dot with underscore
    name = re.sub(r'[^\w.-]', '_', name)
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name).strip('_')
    return name + ext


def download_embedded_images(
    html: str,
    dest_dir: Path,
    session: requests.Session,
) -> str:
    """
    Download all pluginfile.php images embedded in section HTML.
    Replaces their src with sanitized relative local paths so Obsidian can render them.
    """
    if not html:
        return html

    img_pattern = re.compile(r'src="(https://learn\.sde\.dk/pluginfile\.php/[^"]+)"')

    def replace_img(match):
        img_url = match.group(1)
        decoded  = unquote(img_url)
        raw_name = Path(urlparse(decoded).path).name
        if not raw_name:
            return match.group(0)
        # FIX: sanitize filename to avoid spaces/parens breaking ![[wikilinks]]
        safe_name = _safe_image_name(raw_name)
        dest = dest_dir / "_images" / safe_name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            download_file(session, img_url, dest)
        return f'src="./_images/{safe_name}"'

    return img_pattern.sub(replace_img, html)


# ─────────────────────────────────────────────
# HTML CLEANING  (runs BEFORE markdown conversion)
# ─────────────────────────────────────────────

def clean_section_html(html: str) -> str:
    """
    Strip clutter from section HTML before converting to Markdown:
      - Video player UI (vjs control bars, modal dialogs, playback rate lists)
      - Prev / Next navigation links
      - "Spring til…" section jump dropdown
      - "Indholdsfortegnelse" table of contents
    """
    if not HAS_BS4 or not html:
        return html

    soup = BeautifulSoup(html, "html.parser")

    # ── Video player junk ──────────────────────────────────────────────────
    vjs_selectors = [
        "[class*='vjs-']",
        "[class*='video-js']",
        ".mediaplugin",
        "div.moodle-has-zindex",
    ]
    for sel in vjs_selectors:
        for el in soup.select(sel):
            el.decompose()

    # ── Prev / Next section navigation ────────────────────────────────────
    # These appear as standalone <a> or <p> containing only section.php links
    for a in soup.find_all("a", href=re.compile(r'/course/section\.php\?id=')):
        parent = a.parent
        # Remove the parent <p>/<div> if it only contains nav links
        if parent and parent.name in ("p", "div", "span"):
            other_text = parent.get_text(separator=" ").strip()
            if len(other_text) < 30:  # short → pure nav, strip it
                parent.decompose()
        else:
            a.decompose()

    # ── "Spring til…" jump menu ────────────────────────────────────────────
    for sel in ("select[name='jumpto']", "div.jumpmenu", ".section-navigation",
                "div[class*='prevnext']", "nav.prevnextmod"):
        for el in soup.select(sel):
            # Walk up to find a containing wrapper and remove that too
            wrapper = el.find_parent(["div", "nav", "p"])
            (wrapper or el).decompose()

    # ── Table of contents ─────────────────────────────────────────────────
    for el in soup.select("#toc, .toc, nav.section-toc"):
        el.decompose()

    # ── "Indholdsfortegnelse" plain-text node ──────────────────────────────
    # Moodle sometimes renders this as a bare text node rather than a proper
    # TOC element, so the selector above misses it.  Strip the parent tag.
    for el in soup.find_all(string=re.compile(r'^\s*Indholdsfortegnelse\s*$')):
        parent = el.parent
        if parent and parent.name in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                                      'div', 'span', 'li'):
            parent.decompose()
        else:
            el.extract()

    # ── "Vælg aktivitet" accessibility prefix inside list bullets ─────────
    # These come from span.sr-only / span.accesshide inside <li> items
    for el in soup.find_all(string=re.compile(r'Vælg aktivitet')):
        if el.parent and el.parent.name in ("span", "a"):
            cls = el.parent.get("class") or []
            if any(c in cls for c in ("sr-only", "accesshide")):
                el.parent.decompose()

    return str(soup)


# ─────────────────────────────────────────────
# HTML → MARKDOWN
# ─────────────────────────────────────────────

def html_to_markdown(html: str) -> str:
    """Convert an HTML string to clean Markdown."""
    if not html:
        return ""

    if HAS_MARKDOWNIFY:
        result = md_convert(
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "nav", "header", "footer"],
        )
    elif h2t:
        converter = h2t.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = False
        converter.body_width = 0
        result = converter.handle(html)
    else:
        # Very basic strip-tags fallback
        result = re.sub(r"<[^>]+>", " ", html)

    # Clean up excessive blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)
    # Remove leftover HTML entities
    result = result.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return result.strip()


# ─────────────────────────────────────────────
# OBSIDIAN VAULT BUILDER
# ─────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """Make a name safe for the filesystem and Obsidian wikilinks (ASCII-only, underscores)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = name.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    name = name.replace("Æ", "Ae").replace("Ø", "Oe").replace("Å", "Aa")
    name = re.sub(r"\s+", "_", name.strip())
    return name[:80]


def sanitize_obsidian_name(name: str) -> str:
    """
    Minimal sanitization for Obsidian note filenames.
    Keeps spaces and Danish characters (Obsidian handles them fine on Windows),
    only strips chars that are illegal in Windows filenames.
    This gives readable graph node names like "Arbejde og mekanisk energi".
    """
    # Remove Windows-illegal chars: < > : " / \ | ? *  and control chars
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    # Collapse whitespace
    name = " ".join(name.split())
    return name[:100].strip()


def _fix_image_wikilinks(md: str) -> str:
    """
    Convert HTML-style image references that survived markdownify into Obsidian wikilinks.
    Handles filenames with parentheses correctly.

    Input:  ![alt](./_images/image_1.png)
    Output: ![[image_1.png]]
    """
    # Match ![any alt](  ./_images/  FILENAME  )
    # The filename may contain balanced parentheses, so we use a non-greedy match
    # that stops at the closing ) which is preceded by the file extension.
    pattern = re.compile(
        r'!\[[^\]]*\]\(\./_images/([^)\s]+)\)'
    )
    return pattern.sub(lambda m: f"![[{m.group(1)}]]", md)


def build_obsidian_vault(
    vault_root: Path,
    course_name: str,
    sections_data: list[dict],
    no_obsidian_config: bool = False,
) -> Path:
    """
    Vault structure:

    vault_root/
    ├── .obsidian/app.json
    └── {Course}/
        ├── _Index.md              (course overview + links to every topic)
        └── {Topic}/
            ├── {Topic name}.md    (section content — named so graph nodes show real names)
            ├── _images/           (embedded images)
            └── {downloaded files}
    """
    subject_dir = vault_root / sanitize_filename(course_name)
    subject_dir.mkdir(parents=True, exist_ok=True)

    if not no_obsidian_config:
        obsidian_cfg = vault_root / ".obsidian"
        obsidian_cfg.mkdir(exist_ok=True)
        (obsidian_cfg / "app.json").write_text(
            json.dumps({
                "legacyEditor": False,
                "livePreview": True,
                "defaultViewMode": "preview",
                "attachmentFolderPath": "./_images",
                "newFileLocation": "current",
                "useMarkdownLinks": False,
            }, indent=2),
            encoding="utf-8",
        )
        # Enable community plugins list (Dataview + Spaced Repetition)
        # Obsidian won't auto-install them, but having these files means
        # the user just needs to click "Trust plugins" after installing them once.
        plugins_dir = obsidian_cfg / "plugins"
        plugins_dir.mkdir(exist_ok=True)

        (obsidian_cfg / "community-plugins.json").write_text(
            json.dumps(["dataview", "obsidian-spaced-repetition"], indent=2),
            encoding="utf-8",
        )

        # Dataview config — enable inline queries and auto-refresh
        dv_dir = plugins_dir / "dataview"
        dv_dir.mkdir(exist_ok=True)
        (dv_dir / "data.json").write_text(
            json.dumps({
                "enableDataviewJs": False,
                "enableInlineDataview": True,
                "enableInlineDataviewJs": False,
                "prettyRenderInlineFields": True,
                "refreshInterval": 2500,
            }, indent=2),
            encoding="utf-8",
        )

        # Spaced Repetition config — set flashcard tag and deck name
        sr_dir = plugins_dir / "obsidian-spaced-repetition"
        sr_dir.mkdir(exist_ok=True)
        (sr_dir / "data.json").write_text(
            json.dumps({
                "flashcardTags": ["#flashcard"],
                "cardCommentOnSameLine": False,
                "burySiblingCards": False,
                "showContextInCards": True,
                "flashcardEasyText": "Let",
                "flashcardGoodText": "God",
                "flashcardHardText": "Svær",
            }, indent=2),
            encoding="utf-8",
        )

    # topic_index: list of (orig_name, folder_safe, note_obsidian_name)
    topic_index: list[tuple[str, str, str]] = []

    for section in sections_data:
        topic_safe      = sanitize_filename(section["name"])
        # FIX: use the actual section name as the note filename so the Obsidian
        # graph shows readable labels instead of "_Notes" for every node.
        note_name       = sanitize_obsidian_name(section["name"])  # e.g. "Arbejde og mekanisk energi"
        topic_dir       = subject_dir / topic_safe
        topic_dir.mkdir(parents=True, exist_ok=True)

        # ── Build note ─────────────────────────────────────────────────────
        tag_str = sanitize_filename(course_name).lower().replace("_", "-")
        lines: list[str] = [
            "---",
            f"subject: {course_name}",
            f'topic: "{section["name"]}"',
            "status: not-started",
            f"tags: [{tag_str}, eksamen]",
            "---",
            "",
            f"# {section['name']}",
            "",
            f"> **Kursus:** [[_Index|{course_name}]] · [[📋 Eksamensforberedelse|📋 Eksamen]]",
            f"> **Kilde:** {section['url']}",
            "",
            "---",
            "",
        ]

        # Main content: clean HTML first, then convert to Markdown
        raw_html = section.get("html_content", "")
        cleaned_html = clean_section_html(raw_html)
        md_body  = html_to_markdown(cleaned_html)

        if md_body:
            # FIX: convert image references to Obsidian wikilinks with correct
            # regex that handles sanitized (paren-free) filenames
            md_body = _fix_image_wikilinks(md_body)
            lines.append(md_body)
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Activities / Resources ─────────────────────────────────────────
        activities = section.get("activities", [])
        if activities:
            lines.append("## 📎 Materialer & Aktiviteter")
            lines.append("")
            for act in activities:
                icon, label = ACTIVITY_TYPES.get(act["type"], ("📌", act["type"]))
                if act.get("local_path"):
                    fname = Path(act["local_path"]).name
                    lines.append(f"- {icon} **{label}** — [[{fname}|{act['name']}]]")
                else:
                    lines.append(f"- {icon} **{label}** — [{act['name']}]({act['url']})")
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Back-link to course index ──────────────────────────────────────
        # We only link back to the index rather than dumping every sibling topic.
        # The _Index.md already provides full course navigation; repeating it on
        # every page is just noise.  Curated cross-links can be added manually.
        lines.append("## 🔗 Relaterede emner")
        lines.append("")
        lines.append(f"- [[_Index|← {course_name}]]")

        note_path = topic_dir / f"{note_name}.md"
        note_path.write_text("\n".join(lines), encoding="utf-8")
        topic_index.append((section["name"], topic_safe, note_name))
        print(f"  [vault] Wrote {note_path.relative_to(vault_root)}")

    # ── _Index.md ─────────────────────────────────────────────────────────
    index_lines: list[str] = [
        f"# {course_name}",
        "",
        f"> Hentet fra [learn.sde.dk]({BASE_URL}) · [[../Home|🏠 Hjem]] · [[📋 Eksamensforberedelse|📋 Eksamen]]",
        "",
        "## Emner",
        "",
    ]
    for orig, safe, note_name in topic_index:
        index_lines.append(f"- [[{note_name}|{orig}]]")
    index_lines += [
        "",
        "---",
        "*Genereret automatisk af `sde_downloader.py`*",
    ]
    (subject_dir / "_Index.md").write_text("\n".join(index_lines), encoding="utf-8")
    print(f"  [vault] Wrote _Index.md")

    # ── Exam dashboard ─────────────────────────────────────────────────────
    build_exam_dashboard(subject_dir, course_name, topic_index, sections_data)

    return subject_dir


# ─────────────────────────────────────────────
# EXAM DASHBOARD
# ─────────────────────────────────────────────

def build_exam_dashboard(
    subject_dir: Path,
    course_name: str,
    topic_index: list[tuple[str, str, str]],
    sections_data: list[dict],
) -> None:
    """
    Generate '📋 Eksamensforberedelse.md' inside subject_dir.

    Contains:
      - Topic status table (all topics as ⬜ Ikke startet by default)
      - Checklist of every assignment
      - Formula / reference files found on disk
      - Flashcard generation hint
      - Optional Dataview query for dynamic status
    """
    tag = sanitize_filename(course_name).lower().split("_")[0]

    L: list[str] = [
        f"# 📋 Eksamensforberedelse — {course_name}",
        "",
        f"> [[_Index|← {course_name}]] · [[📅 Afleveringsfrister|📅 Frister]] · [[../Home|🏠 Hjem]]",
        "> Sæt `status` i hvert emnes frontmatter: `not-started` → `in-progress` → `ready`",
        "",
        "---",
        "",
        "## 🎯 Emnestatus",
        "",
        "| Emne | Status |",
        "|------|--------|",
    ]

    for orig, safe, note_name in topic_index:
        L.append(f"| [[{note_name}\\|{orig}]] | ⬜ Ikke startet |")

    L += [
        "",
        "> 💡 Har du **Dataview**-pluginet? Se automatisk opdateret tabel nederst.",
        "",
        "---",
        "",
        "## 📝 Afleveringer & opgaver",
        "",
        "*Sæt kryds efterhånden som du gennemgår dem.*",
        "",
    ]

    for section in sections_data:
        for act in section.get("activities", []):
            if act["type"] == "assign":
                note_name = sanitize_obsidian_name(act["name"])
                L.append(f"- [ ] [[{note_name}|{act['name']}]] — _{section['name']}_")

    # Formula/reference files: scan the written subject dir
    L += ["", "---", "", "## 📄 Formelsamlinger & referencer", ""]
    formula_files: list[str] = []
    seen: set[str] = set()
    if subject_dir.exists():
        for f in subject_dir.rglob("*"):
            if f.suffix.lower() in (".pdf", ".docx", ".pptx") and f.name not in seen:
                nl = f.name.lower()
                if any(kw in nl for kw in (
                    "formel", "pensum", "facit", "svar", "besvarelse",
                    "reference", "syrebase", "formelsaml",
                )):
                    seen.add(f.name)
                    formula_files.append(f.name)

    if formula_files:
        for fname in sorted(formula_files):
            L.append(f"- [[{fname}|{Path(fname).stem}]]")
    else:
        L.append("*Ingen fundet automatisk — tilføj manuelt.*")

    L += [
        "",
        "---",
        "",
        "## 🃏 Flashcards",
        "",
        f'*Kør `python sde_downloader.py --subject "{course_name}" --flashcards`'
        " for at generere AI-flashcards per emne.*",
        "",
        "---",
        "",
        "## 🔧 Dataview — automatisk emnestatus",
        "",
        "> Kræver [Dataview-pluginet](https://github.com/blacksmithgu/obsidian-dataview).",
        "",
        "```dataview",
        'TABLE status AS "Status", topic AS "Emne"',
        f'FROM "{subject_dir.name}"',
        f'WHERE contains(tags, "{tag}")',
        "SORT topic ASC",
        "```",
    ]

    out = subject_dir / "📋 Eksamensforberedelse.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  [vault] Wrote 📋 Eksamensforberedelse.md")


# ─────────────────────────────────────────────
# DEADLINES NOTE
# ─────────────────────────────────────────────

def build_deadlines_note(
    subject_dir: Path,
    course_name: str,
    sections_data: list[dict],
) -> None:
    """
    Generate '📅 Afleveringsfrister.md' inside subject_dir.
    Lists all assignments with their due dates as a sortable table.
    Always generated — no flag required.
    """
    all_assignments: list[dict] = []
    for section in sections_data:
        for act in section.get("activities", []):
            if act["type"] == "assign":
                all_assignments.append({
                    "name":      act["name"],
                    "note_name": sanitize_obsidian_name(act["name"]),
                    "topic":     section["name"],
                    "deadline":  act.get("deadline", ""),
                    "url":       act["url"],
                })

    if not all_assignments:
        return

    known   = [a for a in all_assignments if a["deadline"]]
    unknown = [a for a in all_assignments if not a["deadline"]]

    lines: list[str] = [
        f"# 📅 Afleveringsfrister — {course_name}",
        "",
        f"> [[_Index|← {course_name}]] · [[📋 Eksamensforberedelse|📋 Eksamen]] · [[../Home|🏠 Hjem]]",
        "> Opdateres automatisk ved næste download.",
        "",
        "---",
        "",
        "## 📋 Oversigt",
        "",
        "| ✅ | Aflevering | Emne | Frist |",
        "|---|-----------|------|-------|",
    ]

    for a in known + unknown:
        frist = a["deadline"] if a["deadline"] else "—"
        lines.append(
            f"| - [ ] | [[{a['note_name']}\\|{a['name']}]] | {a['topic']} | {frist} |"
        )

    lines += [
        "",
        "---",
        "",
        "*Genereret automatisk af `sde_downloader.py`*",
    ]

    out = subject_dir / "📅 Afleveringsfrister.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [deadlines] Wrote 📅 Afleveringsfrister.md ({len(all_assignments)} assignment(s))")


# ─────────────────────────────────────────────
# HOME PAGE
# ─────────────────────────────────────────────

def update_home_page(vault_root: Path) -> None:
    """
    Regenerate Home.md at the vault root.

    Scans every subdirectory for a _Index.md, reads its topic list, and writes
    a master overview linking all subjects.  Safe to call multiple times — it
    rewrites the file from scratch each run so it stays in sync.
    """
    subject_entries: list[tuple[str, str, list[str]]] = []  # (display_name, folder_name, [topic_names])

    for subdir in sorted(vault_root.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(".") or subdir.name.startswith("_"):
            continue
        index_path = subdir / "_Index.md"
        if not index_path.exists():
            continue

        text = index_path.read_text(encoding="utf-8")

        # Extract display name from the H1 heading
        h1_match = re.match(r'# (.+)', text)
        display_name = h1_match.group(1).strip() if h1_match else subdir.name

        # Extract topic list: lines matching "- [[Note|DisplayName]]"
        topics = re.findall(r'- \[\[.+?\|(.+?)\]\]', text)

        subject_entries.append((display_name, subdir.name, topics))

    if not subject_entries:
        return

    lines: list[str] = [
        "# 🏠 Studieoversigt",
        "",
        "> Hurtig adgang til alle fag og emner.",
        "",
        "---",
        "",
        "## 📚 Fag",
        "",
    ]

    for display_name, folder_name, topics in subject_entries:
        lines.append(f"### [[{folder_name}/_Index|{display_name}]]")
        lines.append("")
        for topic in topics:
            lines.append(f"- [[{topic}|{topic}]]")
        lines.append("")

    # Link to concept map if it already exists
    if (vault_root / "🔗 Begrebskort.md").exists():
        lines.insert(3, "> [[🔗 Begrebskort|🔗 Tværfaglige begreber]]")

    home_path = vault_root / "Home.md"
    home_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [vault] Updated Home.md  ({len(subject_entries)} subject(s))")


# ─────────────────────────────────────────────
# CROSS-SUBJECT CONCEPT MAP
# ─────────────────────────────────────────────

def collect_topic_summaries(vault_root: Path) -> list[dict]:
    """
    Scan vault_root for all subject folders and return topic metadata.
    Returns list of {subject, folder, note_name, summary}.
    """
    topics: list[dict] = []
    for subdir in sorted(vault_root.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(".") or subdir.name.startswith("_"):
            continue
        index_path = subdir / "_Index.md"
        if not index_path.exists():
            continue
        try:
            text = index_path.read_text(encoding="utf-8")
        except Exception:
            continue
        h1_match = re.match(r'# (.+)', text)
        subject_display = h1_match.group(1).strip() if h1_match else subdir.name

        for topic_dir in sorted(subdir.iterdir()):
            if not topic_dir.is_dir() or topic_dir.name.startswith("_"):
                continue
            for md_file in sorted(topic_dir.glob("*.md")):
                if any(x in md_file.stem for x in ["Flashcards", "Prøveeksamen", "AI Studienoter"]):
                    continue
                try:
                    content = md_file.read_text(encoding="utf-8")
                    # Skip frontmatter, extract first meaningful body lines
                    body_lines: list[str] = []
                    in_fm = True
                    fm_seen = 0
                    for line in content.split("\n"):
                        stripped = line.strip()
                        if stripped == "---":
                            fm_seen += 1
                            if fm_seen >= 2:
                                in_fm = False
                            continue
                        if in_fm:
                            continue
                        if stripped.startswith("#") or stripped.startswith(">") or not stripped:
                            continue
                        body_lines.append(stripped)
                        if len(body_lines) >= 6:
                            break
                    summary = " ".join(body_lines)[:350] or "(ingen indhold)"
                    topics.append({
                        "subject":   subject_display,
                        "folder":    subdir.name,
                        "note_name": md_file.stem,
                        "summary":   summary,
                    })
                    break  # one main note per topic folder
                except Exception:
                    pass
    return topics


def build_concept_map_note(vault_root: Path, provider: str = "claude", model: str | None = None) -> None:
    """
    Scan all subjects in vault_root, use Claude to identify shared concepts,
    and write '🔗 Begrebskort.md' at the vault root.
    Skips gracefully if API key is missing or fewer than 2 subjects exist.
    """
    topics = collect_topic_summaries(vault_root)
    subjects = sorted({t["subject"] for t in topics})

    if len(subjects) < 2:
        print(f"  [concepts] Need ≥ 2 subjects in vault (found {len(subjects)}) — skipping")
        return

    print(f"  [concepts] Analysing {len(topics)} topics across {len(subjects)} subject(s) …")

    # Give Claude the exact note names so it can generate correct wikilinks
    input_lines = [
        "Emner med præcise note-navne (brug NØJAGTIGT disse i [[wikilinks]]):"
    ]
    for t in topics:
        input_lines.append(
            f"- Fag: {t['subject']} | note_name: \"{t['note_name']}\" | {t['summary'][:250]}"
        )
    input_text = "\n".join(input_lines)[:14000]

    prompt = (
        f"Du er gymnasielærer og skal hjælpe en elev med at se tværfaglige sammenhænge.\n\n"
        f"Fagene i elevens vault: {', '.join(subjects)}.\n"
        f"Identificer 5-10 begreber, metoder eller fænomener der optræder i mindst 2 fag.\n\n"
        f"Skriv svaret som Obsidian Markdown — PRÆCIS dette format:\n\n"
        f"## [Begrebsnavn]\n"
        f"*[1-linje forklaring af begrebet og dets tværfaglige relevans]*\n\n"
        f"- [[note_name|Fagnavn: note_name]]\n"
        f"- [[note_name|Fagnavn: note_name]]\n\n"
        f"---\n\n"
        f"Regler:\n"
        f"- Brug KUN note-navne fra listen nedenfor (præcist som skrevet efter 'note_name:')\n"
        f"- Medtag KUN begreber der optræder i MINDST 2 forskellige fag\n"
        f"- Skriv begrebsnavne og forklaringer på dansk\n\n"
        f"{input_text}"
    )

    result = _call_ai(prompt, max_tokens=2500, provider=provider, model=model)

    lines: list[str] = [
        "# 🔗 Begrebskort — Tværfaglige Sammenhænge",
        "",
        f"> [[Home|🏠 Hjem]] · *{len(subjects)} fag · {len(topics)} emner analyseret*",
        "> Fælles begreber og metoder på tværs af fag, identificeret af AI.",
        "",
        "---",
        "",
    ]

    if result:
        lines.append(result.strip())
        print(f"  [concepts] Concept map written ✓")
    else:
        lines.append(
            "*Konceptkort ikke tilgængeligt — tilføj `ANTHROPIC_API_KEY` til `.env`.*"
        )

    lines += [
        "",
        "---",
        "",
        f"*Genereret automatisk — fag analyseret: {', '.join(subjects)}*",
    ]

    out = vault_root / "🔗 Begrebskort.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [concepts] Wrote 🔗 Begrebskort.md")


# ─────────────────────────────────────────────
# SYNC MANIFEST
# ─────────────────────────────────────────────

_MANIFEST_NAME = ".sde_sync_manifest.json"


def _section_hash(section: dict) -> str:
    """Stable hash of a scraped section — used to detect remote changes."""
    blob = json.dumps(
        {
            "name": section.get("name", ""),
            "url":  section.get("url", ""),
            "activities": [
                {"name": a["name"], "url": a["url"], "type": a["type"]}
                for a in section.get("activities", [])
            ],
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def load_manifest(subject_dir: Path) -> dict:
    """Load the sync manifest for a subject, or return empty dict."""
    path = subject_dir / _MANIFEST_NAME
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_manifest(subject_dir: Path, manifest: dict) -> None:
    """Persist the sync manifest."""
    path = subject_dir / _MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download SDE Moodle content → Obsidian vault",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--subject", "-s",
                        help="Subject / course name  (e.g. 'Fysik A')")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Download ALL enrolled courses in one run")
    parser.add_argument("--topic",   "-t", default=None,
                        help="Specific topic / section  (downloads ALL if omitted)")
    parser.add_argument("--output",  "-o",
                        default=str(Path(__file__).parent / "vault"),
                        help="Output vault root directory")
    parser.add_argument("--list",    "-l", action="store_true",
                        help="List all available courses and exit")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (no window)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip sections whose vault note already exists (useful after interruptions)")
    parser.add_argument("--sync", action="store_true",
                        help="Re-scrape only sections whose content changed since last run "
                             "(detected via content hash — faster than a full re-download)")
    parser.add_argument("--ai-notes", action="store_true",
                        help="Generate AI exam study notes for each section after scraping")
    parser.add_argument("--flashcards", action="store_true",
                        help="Generate Obsidian Spaced Repetition flashcards per section")
    parser.add_argument("--quiz-notes", action="store_true",
                        help="Scrape quiz questions from completed attempts and save as notes")
    parser.add_argument("--practice-exam", action="store_true",
                        help="Generate AI practice exam per section")
    parser.add_argument("--concept-map", action="store_true",
                        help="Build cross-subject concept map from the whole vault "
                             "(requires ≥2 subjects in vault)")
    parser.add_argument("--ai-provider", default="claude",
                        choices=["claude", "openai", "gemini"],
                        help="AI provider for notes/flashcards/exams (default: claude)")
    parser.add_argument("--ai-model", default=None,
                        help="Override the AI model name (e.g. gpt-4o, gemini-1.5-pro, "
                             "claude-sonnet-4-6). Leave blank to use each provider's default.")
    parser.add_argument("--no-obsidian-config", action="store_true",
                        help="Skip creating the .obsidian/ folder — output is plain Markdown "
                             "compatible with Logseq, Typora, VS Code + Foam, Zettlr, etc.")
    args = parser.parse_args()

    # ── Load credentials ──────────────────────────────────────────────────
    load_dotenv()
    email    = os.getenv("EMAIL") or os.getenv("SDE_USERNAME")
    password = os.getenv("PASSWORD") or os.getenv("SDE_PASSWORD")

    if not email or not password:
        print("ERROR: Create a .env file with EMAIL and PASSWORD (see .env.example)")
        sys.exit(1)

    if not args.subject and not args.list and not getattr(args, "all", False):
        parser.print_help()
        sys.exit(1)

    print(f"\n{'═'*60}")
    print("  SDE Moodle Downloader → Obsidian Vault")
    print(f"{'═'*60}")
    if args.subject:
        print(f"  Subject : {args.subject}")
        print(f"  Topic   : {args.topic or '(alle emner)'}")
    elif getattr(args, "all", False):
        print(f"  Subject : (alle kurser)")
    print(f"  Output  : {args.output}")
    ai_flags = [f for f in ["ai_notes", "flashcards", "practice_exam"] if getattr(args, f, False)]
    if ai_flags:
        print(f"  AI      : {args.ai_provider} / {args.ai_model or 'default model'}")
    print(f"{'═'*60}\n")

    driver = None
    try:
        driver = setup_driver(headless=args.headless)
        wait   = WebDriverWait(driver, 20)

        # ── 1. Login ──────────────────────────────────────────────────────
        login(driver, wait, email, password)

        # Let the login finish and load the courses
        time.sleep(2)

        # ── 2. Course list ────────────────────────────────────────────────
        courses = get_course_list(driver, wait)

        if args.list:
            print("\nTilgængelige kurser:")
            for name, cid in sorted(courses.items()):
                print(f"  [{cid:6d}]  {name}")
            return

        # ── 3. Build course queue (--all or single subject) ───────────────
        vault_root = Path(args.output)
        if getattr(args, "all", False):
            course_queue = list(courses.items())
            print(f"[+] Downloading all {len(course_queue)} enrolled course(s)\n")
        else:
            course_name, course_id = find_course(courses, args.subject)
            if not course_id:
                print(f"\nERROR: Found no course matching '{args.subject}'")
                print("Available courses:")
                for n in sorted(courses):
                    print(f"  - {n}")
                sys.exit(1)
            course_queue = [(course_name, course_id)]

        grand_total_files = 0
        vault_dir = None

        for course_name, course_id in _make_progress(course_queue, desc="Courses", total=len(course_queue)):
            print(f"\n{'─'*60}")
            print(f"[+] Course: '{course_name}'  (id={course_id})")
            print(f"{'─'*60}\n")

            # ── 4. Sections ───────────────────────────────────────────────
            sections = get_sections(driver, wait, course_id)

            if args.topic:
                q = args.topic.lower()
                sections = [s for s in sections if q in s["name"].lower()]
                if not sections:
                    print(f"  [!] No section matching '{args.topic}' — skipping course")
                    continue
                print(f"[+] Filtered to {len(sections)} section(s)\n")

            subject_safe = sanitize_filename(course_name)
            subject_dir  = vault_root / subject_safe
            subject_dir.mkdir(parents=True, exist_ok=True)

            # ── 5. Scrape section content (with resume / sync filtering) ──
            if args.sync:
                # Sync mode: scrape ALL sections, then skip unchanged ones
                print(f"[+] Scraping {len(sections)} section(s) for sync check …\n")
                raw_sections_data: list[dict] = []
                for sec in _make_progress(sections, desc="  Scraping", total=len(sections)):
                    raw_sections_data.append(get_section_content(driver, wait, sec))

                manifest = load_manifest(subject_dir)
                sections_data = []
                skipped = 0
                for sd in raw_sections_data:
                    new_hash = _section_hash(sd)
                    if manifest.get(sd["name"]) == new_hash:
                        print(f"  [sync] Unchanged: '{sd['name']}' — skipping")
                        skipped += 1
                    else:
                        manifest[sd["name"]] = new_hash
                        sections_data.append(sd)
                save_manifest(subject_dir, manifest)
                print(f"[sync] {skipped} unchanged, {len(sections_data)} to process\n")

            elif args.resume:
                filtered_secs: list[dict] = []
                for sec in sections:
                    note_path = (
                        subject_dir
                        / sanitize_filename(sec["name"])
                        / f"{sanitize_obsidian_name(sec['name'])}.md"
                    )
                    if note_path.exists():
                        print(f"  [resume] Skipping '{sec['name']}' (already in vault)")
                    else:
                        filtered_secs.append(sec)
                sections = filtered_secs
                print(f"[resume] {len(sections)} section(s) left to scrape\n")

                print(f"[+] Scraping {len(sections)} section(s) …\n")
                sections_data = []
                for sec in _make_progress(sections, desc="  Scraping", total=len(sections)):
                    sections_data.append(get_section_content(driver, wait, sec))

            else:
                print(f"[+] Scraping {len(sections)} section(s) …\n")
                sections_data = []
                for sec in _make_progress(sections, desc="  Scraping", total=len(sections)):
                    sections_data.append(get_section_content(driver, wait, sec))

                # On a plain (non-sync, non-resume) run, still update the manifest
                manifest = load_manifest(subject_dir)
                for sd in sections_data:
                    manifest[sd["name"]] = _section_hash(sd)
                save_manifest(subject_dir, manifest)

            # ── 6. Download files ─────────────────────────────────────────
            print(f"\n[+] Downloading files …\n")
            session     = get_requests_session(driver)
            total_files = 0

            for section_data in _make_progress(sections_data, desc="  Files", total=len(sections_data)):
                topic_safe = sanitize_filename(section_data["name"])
                topic_dir  = subject_dir / topic_safe

                if section_data["html_content"]:
                    section_data["html_content"] = download_embedded_images(
                        section_data["html_content"], topic_dir, session
                    )

                for activity in section_data["activities"]:
                    act_type = activity["type"]

                    # ── Resource / Folder ────────────────────────────────────
                    if act_type in ("resource", "folder"):
                        print(f"  [{section_data['name']}] ↓ {activity['name']}")
                        file_url = get_resource_file_url(driver, activity["url"])
                        if not file_url:
                            print(f"    [!] Could not resolve download URL")
                            continue

                        raw_name = unquote(Path(urlparse(file_url).path).name)
                        if not raw_name or "." not in raw_name:
                            raw_name = sanitize_filename(activity["name"]) + ".pdf"
                        dest = topic_dir / raw_name

                        if dest.exists():
                            print(f"    ✓ Already exists, skipping")
                            activity["local_path"] = str(dest)
                            activity["downloaded"] = True
                            continue

                        if download_file(session, file_url, dest):
                            activity["local_path"] = str(dest)
                            activity["downloaded"] = True
                            total_files += 1
                            print(f"    ✓ Saved: {raw_name}")
                        else:
                            print(f"    ✗ Failed")

                        if total_files % 10 == 0:
                            session = get_requests_session(driver)

                    # ── Page ─────────────────────────────────────────────────
                    elif act_type == "page":
                        note_name = sanitize_obsidian_name(activity["name"])
                        dest = topic_dir / f"{note_name}.md"

                        if dest.exists():
                            activity["local_path"] = str(dest)
                            activity["downloaded"] = True
                            continue

                        print(f"  [{section_data['name']}] 📃 {activity['name']}")
                        html = get_page_content(driver, activity["url"])
                        if html:
                            cleaned = clean_section_html(html)
                            md_body = html_to_markdown(cleaned)
                            md_body = _fix_image_wikilinks(
                                download_embedded_images(cleaned, topic_dir, session)
                                if "pluginfile.php" in cleaned else md_body
                            )
                            note_lines = [
                                f"# {activity['name']}",
                                "",
                                f"> **Kilde:** {activity['url']}",
                                "",
                                "---",
                                "",
                                md_body,
                            ]
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_text("\n".join(note_lines), encoding="utf-8")
                            activity["local_path"] = str(dest)
                            activity["downloaded"] = True
                            print(f"    ✓ Saved: {dest.name}")

                    # ── Assignment ───────────────────────────────────────────
                    elif act_type == "assign":
                        note_name = sanitize_obsidian_name(activity["name"])
                        note_dest = topic_dir / f"{note_name}.md"

                        if note_dest.exists():
                            activity["local_path"] = str(note_dest)
                            activity["downloaded"] = True
                            continue

                        print(f"  [{section_data['name']}] 📝 {activity['name']}")
                        desc_html, file_urls, deadline = get_assign_content(driver, activity["url"])
                        activity["deadline"] = deadline
                        if deadline:
                            print(f"    📅 Tidsfrist: {deadline}")

                        file_links: list[str] = []
                        for furl in file_urls:
                            raw_name = unquote(Path(urlparse(furl).path).name)
                            if not raw_name or "." not in raw_name:
                                continue
                            fdest = topic_dir / raw_name
                            fdest.parent.mkdir(parents=True, exist_ok=True)
                            if not fdest.exists():
                                if download_file(session, furl, fdest):
                                    total_files += 1
                                    print(f"    ✓ Saved: {raw_name}")
                                else:
                                    continue
                            file_links.append(raw_name)

                        grade, feedback_md, sub_files = "", "", []
                        try:
                            grade, feedback_md, sub_files = get_assign_feedback(driver, activity["url"])
                        except Exception:
                            pass

                        desc_md = html_to_markdown(clean_section_html(desc_html)) if desc_html else ""
                        note_lines = [
                            f"# {activity['name']}",
                            "",
                            f"> **Opgave:** {activity['url']}",
                            "",
                            "---",
                            "",
                        ]
                        if desc_md:
                            note_lines += [desc_md, "", "---", ""]
                        if file_links:
                            note_lines += ["## 📎 Vedhæftede filer", ""]
                            for fname in file_links:
                                note_lines.append(f"- [[{fname}]]")
                            note_lines.append("")
                        if sub_files:
                            note_lines += ["## 📤 Din aflevering", ""]
                            for name, href in sub_files:
                                note_lines.append(f"- [{name}]({href})")
                            note_lines.append("")
                        if grade or feedback_md:
                            note_lines += ["## 🏆 Lærerens feedback", ""]
                            if grade:
                                note_lines += [f"**Karakter:** {grade}", ""]
                            if feedback_md:
                                note_lines += [feedback_md, ""]

                        note_dest.parent.mkdir(parents=True, exist_ok=True)
                        note_dest.write_text("\n".join(note_lines), encoding="utf-8")
                        activity["local_path"] = str(note_dest)
                        activity["downloaded"] = True
                        print(f"    ✓ Note: {note_dest.name}")

                        if total_files % 10 == 0:
                            session = get_requests_session(driver)

                    # ── Quiz ─────────────────────────────────────────────────
                    elif act_type == "quiz" and args.quiz_notes:
                        note_name = sanitize_obsidian_name(activity["name"])
                        dest = topic_dir / f"{note_name}.md"

                        if dest.exists():
                            activity["local_path"] = str(dest)
                            activity["downloaded"] = True
                            continue

                        print(f"  [{section_data['name']}] ❓ {activity['name']}")
                        intro_html, questions = get_quiz_content(driver, activity["url"])

                        note_lines: list[str] = [
                            f"# {activity['name']}",
                            "",
                            f"> **Quiz:** {activity['url']}",
                            "",
                            "---",
                            "",
                        ]
                        if intro_html:
                            intro_md = html_to_markdown(clean_section_html(intro_html))
                            if intro_md:
                                note_lines += [intro_md, "", "---", ""]

                        if questions:
                            note_lines += ["## ❓ Spørgsmål", ""]
                            for i, q in enumerate(questions, 1):
                                note_lines.append(f"### {i}. {q['text']}")
                                note_lines.append("")
                                if q["options"]:
                                    for opt in q["options"]:
                                        mark = "✅" if opt["is_correct"] else "○"
                                        note_lines.append(f"- {mark} {opt['text']}")
                                    note_lines.append("")
                                if q["answer"]:
                                    note_lines += [f"> **{q['answer']}**", ""]
                                note_lines.append("---")
                                note_lines.append("")
                        else:
                            note_lines += [
                                "## ❓ Spørgsmål",
                                "",
                                "*Gennemfør quizzen i Moodle for at se spørgsmål her.*",
                                "",
                            ]

                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text("\n".join(note_lines), encoding="utf-8")
                        activity["local_path"] = str(dest)
                        activity["downloaded"] = True
                        print(f"    ✓ Note: {dest.name}")

            grand_total_files += total_files

            # ── 7. Build Obsidian vault ───────────────────────────────────
            print(f"\n[+] Building Obsidian vault …\n")
            vault_dir = build_obsidian_vault(vault_root, course_name, sections_data, no_obsidian_config=args.no_obsidian_config)
            build_deadlines_note(vault_dir, course_name, sections_data)

            # ── 8. AI study notes + flashcards + practice exam ─────────────
            if args.ai_notes or args.flashcards or args.practice_exam:
                print(f"\n[+] Generating AI content ({args.ai_provider}) …\n")
                for section_data in _make_progress(sections_data, desc="  AI", total=len(sections_data)):
                    topic_safe = sanitize_filename(section_data["name"])
                    note_name  = sanitize_obsidian_name(section_data["name"])
                    topic_dir  = vault_dir / topic_safe

                    # Collect text: HTML + .md notes + downloaded PDFs
                    parts: list[str] = []
                    raw_html = section_data.get("html_content", "")
                    if raw_html:
                        parts.append(html_to_markdown(clean_section_html(raw_html)))
                    for act in section_data.get("activities", []):
                        lp = act.get("local_path")
                        if not lp:
                            continue
                        lp_path = Path(lp)
                        if lp_path.suffix == ".md":
                            try:
                                parts.append(lp_path.read_text(encoding="utf-8"))
                            except Exception:
                                pass
                        elif lp_path.suffix.lower() == ".pdf":
                            pdf_text = extract_pdf_text(lp_path)
                            if pdf_text:
                                parts.append(f"[PDF: {lp_path.name}]\n{pdf_text}")

                    # Also scan the whole topic folder for any PDFs not linked as activities
                    if topic_dir.exists():
                        for pdf_file in topic_dir.glob("*.pdf"):
                            pdf_text = extract_pdf_text(pdf_file)
                            if pdf_text:
                                parts.append(f"[PDF: {pdf_file.name}]\n{pdf_text}")

                    combined = "\n\n".join(p for p in parts if p.strip())
                    if not combined.strip():
                        print(f"  [skip] No text content for '{section_data['name']}'")
                        continue

                    # ── AI study notes ─────────────────────────────────────
                    if args.ai_notes:
                        print(f"  [AI notes] {section_data['name']}")
                        note_path = topic_dir / f"{note_name}.md"
                        if note_path.exists():
                            existing = note_path.read_text(encoding="utf-8")
                            if "## 🤖 AI Studienoter" not in existing:
                                ai_text = generate_ai_notes(combined, section_data["name"], course_name, args.ai_provider, args.ai_model)
                                note_path.write_text(
                                    existing.rstrip() + "\n\n---\n\n## 🤖 AI Studienoter\n\n" + ai_text + "\n",
                                    encoding="utf-8",
                                )
                                print(f"    ✓ Embedded in {note_path.name}")
                            else:
                                print(f"    – Already has AI notes, skipping")
                        else:
                            ai_text = generate_ai_notes(combined, section_data["name"], course_name, args.ai_provider, args.ai_model)
                            ai_path = topic_dir / f"{note_name} – AI Studienoter.md"
                            ai_path.parent.mkdir(parents=True, exist_ok=True)
                            ai_path.write_text(ai_text, encoding="utf-8")
                            print(f"    ✓ {ai_path.name}")

                    # ── Flashcards ─────────────────────────────────────────
                    if args.flashcards:
                        fc_path = topic_dir / f"{note_name} – Flashcards.md"
                        if fc_path.exists():
                            print(f"  [flashcards] {section_data['name']} — already exists, skipping")
                        else:
                            print(f"  [flashcards] {section_data['name']}")
                            fc_text = generate_flashcards(combined, section_data["name"], course_name, args.ai_provider, args.ai_model)
                            fc_path.parent.mkdir(parents=True, exist_ok=True)
                            fc_path.write_text(fc_text, encoding="utf-8")
                            print(f"    ✓ {fc_path.name}")

                    # ── Practice exam ──────────────────────────────────────
                    if args.practice_exam:
                        exam_path = topic_dir / f"{note_name} – Prøveeksamen.md"
                        if exam_path.exists():
                            print(f"  [exam] {section_data['name']} — already exists, skipping")
                        else:
                            print(f"  [exam] {section_data['name']}")
                            exam_text = generate_practice_exam(combined, section_data["name"], course_name, args.ai_provider, args.ai_model)
                            exam_path.parent.mkdir(parents=True, exist_ok=True)
                            exam_path.write_text(exam_text, encoding="utf-8")
                            print(f"    ✓ {exam_path.name}")

        # ── 9. Home page + concept map (after all courses done) ───────────
        update_home_page(vault_root)
        if args.concept_map:
            print(f"\n[+] Building cross-subject concept map …\n")
            build_concept_map_note(vault_root, args.ai_provider, args.ai_model)

        print(f"\n{'═'*60}")
        print(f"  ✓ Done!")
        if vault_dir:
            print(f"  Vault   : {vault_dir.parent}")
        print(f"  Courses : {len(course_queue)}")
        print(f"  Files   : {grand_total_files} downloaded")
        print(f"{'═'*60}")
        print(f"\nOpen Obsidian, click 'Open folder as vault', and select:\n  {args.output}\n")

    except KeyboardInterrupt:
        print("\n[!] Interrupted")
    except Exception:
        print("\n[!] Fatal error:")
        traceback.print_exc()
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
