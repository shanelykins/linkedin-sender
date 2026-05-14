#!/usr/bin/env python3
"""
LinkedIn Outbound Sender - Send messages from Thoughtful waves.

Usage:
    python sender.py waves                    # List available waves
    python sender.py preview wave-6           # Preview messages in a wave
    python sender.py send wave-6 --dry-run    # Dry run (opens profiles, shows what would send)
    python sender.py send wave-6              # Send messages (with confirmation)
    python sender.py send wave-6 --start 3    # Start from contact #3
    python sender.py send wave-6 --headless   # Run without visible browser (risky)
"""

import argparse
import json
import os
import re
import sys
import time
import random
import tty
import termios
from dataclasses import dataclass
from typing import Optional

import requests


def get_keypress():
    """Get a single keypress from the user (no Enter required)."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

# ============================================================================
# Configuration
# ============================================================================

THOUGHTFUL_API_BASE = "https://www.thoughtful.app/api/v1"
THOUGHTFUL_API_KEY = os.environ.get("THOUGHTFUL_API_KEY", "")

# Browser profile persistence
BROWSER_DATA_DIR = os.path.expanduser("~/.linkedin-sender-browser")

# Timing (seconds) - balanced for speed vs detection
DELAY_BETWEEN_MESSAGES = (30, 60)   # Random delay between sends
DELAY_PAGE_LOAD = 2.5               # Wait for LinkedIn page to load
DELAY_AFTER_CLICK = 1.0             # Wait after clicking buttons
DELAY_MODAL_OPEN = 1.5              # Wait for message modal to open
TYPING_DELAY_MS = (20, 50)          # Milliseconds between keystrokes


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Contact:
    """A contact to message."""
    number: int
    name: str
    company: str
    linkedin_url: str
    message: str
    signal: str = ""
    slug: Optional[str] = None  # Thoughtful page slug for status updates


@dataclass
class Wave:
    """A wave of outreach contacts."""
    slug: str
    title: str
    contacts: list


# ============================================================================
# Thoughtful API Client
# ============================================================================

class ThoughtfulClient:
    """Client for Thoughtful API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _get(self, path: str, **kwargs) -> dict:
        resp = self.session.get(f"{THOUGHTFUL_API_BASE}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, data: dict) -> dict:
        resp = self.session.patch(f"{THOUGHTFUL_API_BASE}{path}", json=data)
        resp.raise_for_status()
        return resp.json()

    def list_waves(self) -> list:
        """List all outreach wave pages."""
        result = self._get("/search", params={"q": "outreach wave", "limit": 50})
        waves = []
        seen = set()
        for item in result.get("results", []):
            slug = item.get("pageSlug", "") or item.get("slug", "")
            title = item.get("pageTitle", "") or item.get("title", "")
            if slug and "outreach" in slug.lower() and "wave" in slug.lower():
                if slug not in seen:
                    seen.add(slug)
                    waves.append({"slug": slug, "title": title})
        return sorted(waves, key=lambda x: x.get("slug", ""))

    def get_wave(self, slug: str) -> Wave:
        """Get a wave with parsed contacts and messages."""
        content = self._get(f"/pages/{slug}/content")
        contacts = self._parse_wave_content(content.get("content", ""))
        return Wave(
            slug=slug,
            title=content.get("title", slug),
            contacts=contacts
        )

    def _parse_wave_content(self, content: str) -> list:
        """Parse wave markdown content into contacts."""
        contacts = []

        # Try Format 1 (WOZCODE): **N.** [Name](linkedin_url) — Company, Title
        contact_pattern = r'\*\*(\d+)\.\*\*\s*\[([^\]]+)\]\((https://www\.linkedin\.com/in/[^)]+)\)\s*[—-]\s*([^,|\n]+)'
        matches = list(re.finditer(contact_pattern, content))

        # Try Format 2 (Thoughtful.app): ## N. Company \n **To:** Name, Title **LinkedIn:** [url](url)
        if not matches:
            # Different pattern for Thoughtful.app format
            contact_pattern2 = r'##\s*(\d+)\.\s*([^\n]+)\n+\*\*To:\*\*\s*([^,\n]+)(?:,\s*[^\*\n]+)?\s*\*\*LinkedIn:\*\*\s*\[?(?:https://www\.linkedin\.com/in/[^\]\s\)]+)?\]?\((https://www\.linkedin\.com/in/[^)]+)\)'
            matches = list(re.finditer(contact_pattern2, content))

            if matches:
                # Parse Format 2
                for i, match in enumerate(matches):
                    num = int(match.group(1))
                    company = match.group(2).strip()
                    name = match.group(3).strip()
                    linkedin_url = match.group(4).strip()

                    # Get section between this contact and next
                    start_pos = match.end()
                    end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                    section = content[start_pos:end_pos]

                    # Extract message from blockquote
                    message_match = re.search(r'>\s*(.+?)(?=\n\n\*\s*\*\s*\*|\n\n##|\n\n---|\Z)', section, re.DOTALL)
                    message = ""
                    if message_match:
                        message = message_match.group(1).strip()
                        message = re.sub(r'\n>\s*', ' ', message)
                        message = re.sub(r'\s+', ' ', message).strip()

                    if message:
                        contacts.append(Contact(
                            number=num,
                            name=name,
                            company=company,
                            linkedin_url=linkedin_url,
                            message=message,
                            signal="",
                            slug=None
                        ))
                return contacts

        for i, match in enumerate(matches):
            num = int(match.group(1))
            name = match.group(2).strip()
            linkedin_url = match.group(3).strip()
            company = match.group(4).strip()

            # Get section between this contact and next
            start_pos = match.end()
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            section = content[start_pos:end_pos]

            # Extract message from blockquote (> text)
            message_match = re.search(r'>\s*(.+?)(?=\n\n\*\*|\n\n---|\n\n\* \*|\n\n##|\n\n\||\Z)', section, re.DOTALL)
            message = ""
            if message_match:
                message = message_match.group(1).strip()
                message = re.sub(r'\n>\s*', ' ', message)  # Join multi-line blockquotes
                message = re.sub(r'\s+', ' ', message).strip()

            # Extract signal
            signal_match = re.search(r'_Signal:\s*(.+?)_', section)
            signal = signal_match.group(1).strip() if signal_match else ""

            # Extract Thoughtful page slug
            slug_match = re.search(r'@\{[^|]+\|([^}]+)\}', section)
            slug = slug_match.group(1) if slug_match else None

            # Also try @Name pattern
            if not slug:
                slug_match2 = re.search(r'@([A-Z][^@\n]+)$', section, re.MULTILINE)
                if slug_match2:
                    # Convert name to slug format
                    raw = slug_match2.group(1).strip()
                    slug = re.sub(r'[^a-z0-9]+', '-', raw.lower()).strip('-')

            if message:  # Only add contacts with messages
                contacts.append(Contact(
                    number=num,
                    name=name,
                    company=company,
                    linkedin_url=linkedin_url,
                    message=message,
                    signal=signal,
                    slug=slug
                ))

        return contacts

    def mark_contacted(self, contact_slug: str) -> bool:
        """Update a contact's stage to 'Contacted' and increment touch_count."""
        if not contact_slug:
            return False
        try:
            page = self._get(f"/pages/{contact_slug}")
            props = page.get("page", {}).get("properties", {})
            touch_count = props.get("touch_count", 0) or 0
            # Ensure touch_count is an integer
            touch_count = int(touch_count) if touch_count else 0

            self._patch(f"/pages/{contact_slug}", {
                "properties": {
                    "stage": "Contacted",
                    "touch_count": touch_count + 1
                }
            })
            return True
        except Exception as e:
            print(f"    [warn] Could not update Thoughtful: {e}")
            return False

    def update_lead_contacted(self, contact, send_date: str = None) -> bool:
        """
        Update a lead in the Leads database to Contacted status.
        Sets: stage=Contacted, notes, source=LinkedIn, next_action, follow_up_date (+7 days)
        """
        from datetime import datetime, timedelta

        if not send_date:
            send_date = datetime.now().strftime("%-m/%-d/%y")

        follow_up_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        # Try to find the lead by name
        try:
            # Search for the contact
            results = self._get("/search", params={"q": contact.name, "limit": 10})
            lead_slug = None

            for r in results.get("results", []):
                # Check if it's in the Leads database (parent is leads)
                slug = r.get("pageSlug", r.get("slug", ""))
                title = r.get("pageTitle", r.get("title", ""))
                if contact.name.lower() in title.lower():
                    # Verify it's a lead page
                    try:
                        page = self._get(f"/pages/{slug}")
                        if page.get("page", {}).get("parentSlug") == "leads":
                            lead_slug = slug
                            break
                    except:
                        continue

            if not lead_slug:
                print(f"    [warn] Lead page not found for {contact.name}")
                return False

            # Update the lead
            self._patch(f"/pages/{lead_slug}", {
                "properties": {
                    "stage": "Contacted",
                    "source": "LinkedIn",
                    "notes": f"Sent LinkedIn DM on {send_date}",
                    "next_action": "Follow up if no reply",
                    "follow_up_date": follow_up_date
                }
            })
            return True

        except Exception as e:
            print(f"    [warn] Could not update lead {contact.name}: {e}")
            return False

    def log_to_weekly_tracker(self, sent_contacts: list, week_slug: str = None) -> bool:
        """
        Log outreach to the weekly progress tracker page.
        Appends to "Outreach Log" section.
        """
        from datetime import datetime, timedelta

        if not sent_contacts:
            return False

        today = datetime.now()

        # Find the current week page if not specified
        if not week_slug:
            # Get the Monday of this week for searching
            days_since_monday = today.weekday()
            monday = today - timedelta(days=days_since_monday)
            week_search = monday.strftime("Week of %b %-d")  # e.g., "Week of May 4"

            try:
                results = self._get("/search", params={"q": week_search, "limit": 10})
                for r in results.get("results", []):
                    slug = r.get("pageSlug", r.get("slug", ""))
                    title = r.get("pageTitle", r.get("title", ""))
                    if slug.startswith("week-of-") and week_search.lower() in title.lower():
                        week_slug = slug
                        break
                # Fallback: try just "week-of-may"
                if not week_slug:
                    for r in results.get("results", []):
                        slug = r.get("pageSlug", r.get("slug", ""))
                        if slug.startswith("week-of-"):
                            week_slug = slug
                            break
            except:
                pass

        if not week_slug:
            print(f"    [warn] Could not find weekly tracker page for {week_search}")
            return False

        try:
            # Get current content
            content_resp = self._get(f"/pages/{week_slug}/content")
            current_content = content_resp.get("content", "")

            # Build outreach log entry
            date_str = today.strftime("%A %-m/%-d")
            entries = [f"- {c.name} ({c.company})" for c in sent_contacts]
            log_entry = f"\n\n### {date_str}\n**Sent: {len(sent_contacts)} LinkedIn DMs**\n" + "\n".join(entries)

            # Check if Outreach Log section exists
            if "## Outreach Log" in current_content:
                # Append after the Outreach Log header
                updated_content = current_content.replace("## Outreach Log", f"## Outreach Log{log_entry}")
            else:
                # Add new section at the end
                updated_content = current_content + f"\n\n## Outreach Log{log_entry}"

            self._patch(f"/pages/{week_slug}", {
                "content": updated_content
            })
            return True

        except Exception as e:
            print(f"    [warn] Could not update weekly tracker: {e}")
            return False

    def update_sales_log(self, sent_contacts: list) -> bool:
        """Append sent contacts to the Sales Log for today."""
        if not sent_contacts:
            return False

        from datetime import datetime

        try:
            # Get current Sales Log content
            content_resp = self._get("/pages/sales-log/content")
            current_content = content_resp.get("content", "")

            # Format today's date like "Wednesday May 6"
            today = datetime.now()
            day_name = today.strftime("%A")
            month_day = today.strftime("%B %-d")  # May 6
            today_header = f"**{day_name} {month_day}**"

            # Build the entries to add
            new_entries = []
            for contact in sent_contacts:
                # Format: - @{Name|slug} — Company
                if contact.slug:
                    entry = f"- @{{{contact.name}|{contact.slug}}} — {contact.company}"
                else:
                    entry = f"- {contact.name} — {contact.company}"
                new_entries.append(entry)

            entries_text = "\n".join(new_entries)

            # Check if today's section already exists
            if today_header in current_content:
                # Append to existing section - find where today's section ends
                # Insert after the header line
                lines = current_content.split("\n")
                new_lines = []
                found_today = False
                inserted = False

                for i, line in enumerate(lines):
                    new_lines.append(line)
                    if today_header in line and not inserted:
                        found_today = True
                    elif found_today and not inserted:
                        # Insert our entries right after the header
                        # But first check if there's already content
                        if line.strip() == "" or line.startswith("- "):
                            # Insert before empty line or first entry
                            new_lines.pop()  # Remove the line we just added
                            new_lines.append(entries_text)
                            new_lines.append(line)  # Re-add the line
                            inserted = True
                        elif line.startswith("**"):
                            # Hit the next day's header, insert before it
                            new_lines.pop()
                            new_lines.append(entries_text)
                            new_lines.append("")
                            new_lines.append(line)
                            inserted = True

                if found_today and not inserted:
                    # Section was at the end, append there
                    new_lines.append(entries_text)

                updated_content = "\n".join(new_lines)
            else:
                # Create new section for today at the top
                # Find where to insert (after the title/intro but before first day section)
                lines = current_content.split("\n")
                insert_idx = 0

                for i, line in enumerate(lines):
                    if line.startswith("**") and any(day in line for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]):
                        insert_idx = i
                        break
                else:
                    # No day sections found, insert at end
                    insert_idx = len(lines)

                new_section = f"\n{today_header}\n{entries_text}\n"
                lines.insert(insert_idx, new_section)
                updated_content = "\n".join(lines)

            # Update the Sales Log
            self._patch("/pages/sales-log/content", {
                "content": updated_content
            })

            return True

        except Exception as e:
            print(f"    [warn] Could not update Sales Log: {e}")
            return False


# ============================================================================
# LinkedIn Automation
# ============================================================================

class LinkedInSender:
    """Playwright-based LinkedIn message sender with robust element detection."""

    def __init__(self, headless: bool = False):
        self.headless = headless
        self.playwright = None
        self.context = None
        self.page = None

    def start(self):
        """Start browser with persistent profile (keeps LinkedIn login)."""
        from playwright.sync_api import sync_playwright
        import subprocess

        # Clean up stale lock files before launching
        lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
        for lock_file in lock_files:
            lock_path = os.path.join(BROWSER_DATA_DIR, lock_file)
            if os.path.exists(lock_path):
                try:
                    os.remove(lock_path)
                except PermissionError:
                    # Try with sudo if regular remove fails
                    subprocess.run(["sudo", "rm", "-f", lock_path], capture_output=True)
                except:
                    pass

        self.playwright = sync_playwright().start()

        # Persistent context reuses your LinkedIn login
        self.context = self.playwright.chromium.launch_persistent_context(
            BROWSER_DATA_DIR,
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            slow_mo=50,
            args=["--disable-blink-features=AutomationControlled"],
        )

        self.page = self.context.new_page()

        # Go to LinkedIn and check login status
        print("Starting browser...")
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        time.sleep(DELAY_PAGE_LOAD)

        if "login" in self.page.url or "checkpoint" in self.page.url:
            print("\n" + "=" * 50)
            print("NOT LOGGED INTO LINKEDIN")
            print("Please log in manually in the browser window.")
            print("Waiting for login (checking every 3 seconds)...")
            print("=" * 50)
            # Poll until logged in (URL changes from login page)
            for _ in range(120):  # Wait up to 6 minutes
                time.sleep(3)
                current_url = self.page.url
                if "login" not in current_url and "checkpoint" not in current_url:
                    print("Login detected!")
                    break
            else:
                print("Timed out waiting for login.")
                raise Exception("LinkedIn login timed out")
            time.sleep(2)

        print("Browser ready.\n")

    def stop(self):
        """Close browser."""
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()

    def send_message(self, contact: Contact, dry_run: bool = False, no_send: bool = False, approve_mode: bool = False) -> tuple:
        """
        Send a message to a contact.
        Returns (success: bool, status_message: str, action: str).
        If no_send=True, prepares message but doesn't click Send.
        If approve_mode=True, prepares message and waits for keypress before sending.
        """
        try:
            # In no_send or approve mode, open a new tab for each contact
            if no_send or approve_mode:
                self.page = self.context.new_page()

            # Navigate to profile
            self.page.goto(contact.linkedin_url, wait_until="domcontentloaded")
            time.sleep(DELAY_PAGE_LOAD)

            # Ensure this tab is in front before interacting
            self.page.bring_to_front()

            # Check for invalid profile
            page_content = self.page.content()
            if "Page not found" in page_content or "this page doesn't exist" in page_content.lower():
                return False, "Profile not found", "error"

            # Find Message button - multiple strategies
            message_btn = self._find_message_button()

            if not message_btn:
                return False, "Could not find Message button", "error"

            # Click Message button
            if message_btn != "already_open":
                message_btn.click()
                time.sleep(DELAY_MODAL_OPEN)

            # Check if we need to select a recipient first (compose modal)
            recipient_selected = self._handle_recipient_selection(contact.name)
            if recipient_selected == "error":
                return False, "Could not select recipient in compose modal", "error"

            # Wait for message compose area to appear after selecting recipient
            time.sleep(2)

            # Find message input
            msg_input = self._find_message_input()

            if not msg_input:
                return False, "Could not find message input field", "error"

            # Handle subject line if present
            has_subject = self._handle_subject_line(contact.message)

            if dry_run:
                print(f"    [DRY RUN] Would send:")
                print(f"    \"{contact.message[:100]}...\"")
                self._close_modal()
                return True, "Dry run - message NOT sent", "dry_run"

            # Type the message with human-like behavior
            msg_to_send = contact.message

            # Clean up message - remove phrases we don't want
            msg_to_send = msg_to_send.replace("Anthropic-approved ", "")
            msg_to_send = msg_to_send.replace("an Anthropic-approved ", "a ")
            msg_to_send = msg_to_send.replace(", an Anthropic-approved", ",")

            if has_subject:
                # Message was split, get remainder
                first_sentence = contact.message.split(".")[0] + "."
                msg_to_send = contact.message[len(first_sentence):].strip() or contact.message

            # Click into message field and type
            print(f"    Clicking message field...")
            try:
                # Bring this tab to front to ensure keyboard focus
                self.page.bring_to_front()
                time.sleep(0.3)

                # Scroll element into view and focus using JavaScript
                msg_input.evaluate("el => { el.scrollIntoView({block: 'center'}); el.focus(); }")
                time.sleep(0.3)

                # Click with force to ensure we're in the field
                try:
                    msg_input.click(timeout=3000, force=True)
                except:
                    # If click fails, try focusing via JS again
                    msg_input.evaluate("el => el.focus()")
                time.sleep(0.3)

                print(f"    Typing message ({len(msg_to_send)} chars)...")

                # Type the message using keyboard
                self.page.keyboard.type(msg_to_send, delay=5)
                time.sleep(0.5)
                print(f"    Message typed.")
            except Exception as e:
                print(f"    [warn] Click/type failed: {e}")
                return False, f"Could not type message: {e}", "error"

            # If no_send mode, stop here - human will review and click Send
            if no_send:
                return True, "Message ready - review and click Send", "prepared"

            # If approve mode, wait for user to approve before sending
            if approve_mode:
                print("\n    ╔══════════════════════════════════════════════════════╗")
                print("    ║  SPACE/ENTER = Send  │  S = Skip  │  Q = Quit        ║")
                print("    ╚══════════════════════════════════════════════════════╝")
                sys.stdout.flush()

                key = get_keypress()

                if key.lower() == 'q':
                    return False, "User quit", "quit"
                elif key.lower() == 's':
                    self._close_modal()
                    return True, "Skipped by user", "skipped"
                elif key in [' ', '\r', '\n']:
                    # User approved - continue to send
                    pass
                else:
                    # Any other key - treat as skip
                    self._close_modal()
                    return True, f"Skipped (pressed '{key}')", "skipped"

            # Find and click Send
            send_btn = self._find_send_button()

            if not send_btn:
                return False, "Could not find Send button", "error"

            send_btn.click()
            time.sleep(DELAY_AFTER_CLICK)

            # Verify send (modal should close or show sent state)
            time.sleep(1)
            return True, "Message sent", "sent"

        except Exception as e:
            return False, f"Error: {str(e)[:100]}", "error"

    def _handle_recipient_selection(self, contact_name: str) -> str:
        """
        Handle the recipient search/selection in compose modal.
        Returns: "selected", "not_needed", or "error"
        """
        # Check if there's a recipient input field (compose modal)
        recipient_selectors = [
            'input[placeholder*="Type a name"]',
            'input[placeholder*="Search"]',
            'input[aria-label*="Type a name"]',
            '.msg-connections-typeahead input',
            'input.msg-connections-typeahead__search-field',
        ]

        recipient_input = None
        for selector in recipient_selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.is_visible(timeout=1500):
                    recipient_input = inp
                    break
            except:
                continue

        if not recipient_input:
            # No recipient field - direct message modal, already addressed to contact
            return "not_needed"

        try:
            # Type the contact's FULL name to search
            print(f"    Searching for recipient: {contact_name}")
            recipient_input.click()
            time.sleep(0.3)
            recipient_input.fill(contact_name)  # Full name for exact match
            time.sleep(2)  # Wait for search results to load

            # The first result should be the best match - click it directly
            # LinkedIn shows results as a list, first one is usually exact match
            first_result_selectors = [
                'ul li:first-child',  # Generic list item
                'div[role="listbox"] > div:first-child',
                '.msg-connections-typeahead__search-results li:first-child',
                'ul[role="listbox"] li:first-child',
            ]

            for selector in first_result_selectors:
                try:
                    result = self.page.locator(selector).first
                    if result.is_visible(timeout=2000):
                        result_text = result.inner_text()
                        # Verify it's the right person (first + last name match)
                        name_parts = contact_name.lower().split()
                        if all(part in result_text.lower() for part in name_parts):
                            print(f"    Clicking on: {result_text[:60]}...")
                            result.click()
                            time.sleep(DELAY_AFTER_CLICK)
                            return "selected"
                except:
                    continue

            # Fallback: just press Enter to select first result
            print(f"    Pressing Enter to select first result...")
            self.page.keyboard.press("Enter")
            time.sleep(DELAY_AFTER_CLICK)
            return "selected"

        except Exception as e:
            print(f"    [warn] Recipient selection issue: {e}")
            return "error"

    def _find_message_button(self):
        """Find the Message button using multiple strategies."""
        selectors = [
            'button:has-text("Message"):visible',
            '[data-control-name="message"]',
            'button[aria-label*="Message"]:visible',
            '.pvs-profile-actions button:has-text("Message")',
            '.pv-top-card-v2-ctas button:has-text("Message")',
            '.pv-top-card__cta-container button:has-text("Message")',
        ]

        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=1000):
                    return btn
            except:
                continue

        # Try "More" dropdown as fallback
        try:
            more_btn = self.page.locator('button:has-text("More"):visible').first
            if more_btn.is_visible(timeout=1000):
                more_btn.click()
                time.sleep(DELAY_AFTER_CLICK)

                msg_option = self.page.locator('div[role="menu"] span:has-text("Message")').first
                if msg_option.is_visible(timeout=1000):
                    msg_option.click()
                    time.sleep(DELAY_MODAL_OPEN)
                    return "already_open"
        except:
            pass

        return None

    def _find_message_input(self):
        """Find the message input field."""
        # Wait a moment for UI to settle after recipient selection
        time.sleep(1)

        selectors = [
            'div.msg-form__contenteditable[contenteditable="true"]',
            'div[role="textbox"][contenteditable="true"]',
            '.msg-form__msg-content-container div[contenteditable="true"]',
            'div.msg-form__message-texteditor div[contenteditable="true"]',
            '.msg-form__compose-container div[contenteditable="true"]',
            'div[data-placeholder="Write a message…"]',
            'div[aria-label="Write a message…"]',
            'p.msg-form__placeholder',  # Sometimes it's a p tag
            '.msg-form__message-texteditor',
        ]

        for selector in selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.is_visible(timeout=1500):
                    print(f"    Found message input with: {selector[:40]}")
                    return inp
            except:
                continue

        # Last resort: any contenteditable in the message form
        try:
            inp = self.page.locator('.msg-form div[contenteditable="true"]').first
            if inp.is_visible(timeout=1500):
                print(f"    Found message input with fallback selector")
                return inp
        except:
            pass

        print(f"    Could not find message input!")
        return None

    def _handle_subject_line(self, message: str) -> bool:
        """Fill subject line if present. Returns True if subject was found."""
        try:
            subj = self.page.locator('input[name="subject"], input[placeholder*="Subject"]').first
            if subj.is_visible(timeout=500):
                first_sentence = message.split(".")[0] + "."
                subj.fill(first_sentence[:100])
                time.sleep(DELAY_AFTER_CLICK)
                return True
        except:
            pass
        return False

    def _find_send_button(self):
        """Find the Send button."""
        selectors = [
            'button.msg-form__send-button:visible',
            'button.msg-form__send-btn:visible',
            'button[type="submit"]:has-text("Send"):visible',
            'footer button:has-text("Send"):visible',
        ]

        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=1000) and btn.is_enabled():
                    return btn
            except:
                continue

        # Broader fallback
        try:
            btn = self.page.locator('button:has-text("Send")').first
            if btn.is_visible(timeout=500) and btn.is_enabled():
                return btn
        except:
            pass

        return None

    def _close_modal(self):
        """Close the message modal."""
        try:
            close_selectors = [
                'button[data-test-modal-close-btn]',
                'button.msg-overlay-bubble-header__control--close-btn',
                'button[aria-label="Close"]',
            ]
            for sel in close_selectors:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=500):
                    btn.click()
                    return
        except:
            pass

        # Fallback: press Escape
        self.page.keyboard.press("Escape")

    def _paste_message(self, text: str):
        """Insert message into the input field."""
        # Bring this tab to front
        self.page.bring_to_front()
        time.sleep(0.2)

        # Find the message input
        input_selectors = [
            'div.msg-form__contenteditable[contenteditable="true"]',
            'div[role="textbox"][contenteditable="true"]',
        ]

        for selector in input_selectors:
            try:
                el = self.page.locator(selector).first
                if el.is_visible(timeout=1000):
                    el.click()
                    time.sleep(0.2)
                    # Use fill() for contenteditable - set innerHTML directly
                    el.evaluate(f'el => el.innerText = {repr(text)}')
                    # Trigger input event so LinkedIn registers the change
                    el.evaluate('el => el.dispatchEvent(new Event("input", {bubbles: true}))')
                    time.sleep(0.2)
                    return
            except Exception as e:
                print(f"    [warn] Paste attempt failed: {e}")
                continue

        # Fallback: try keyboard typing (slower but reliable)
        print(f"    Using keyboard fallback...")
        self.page.keyboard.type(text, delay=10)

    def _type_human_like(self, element, text: str):
        """Type text with human-like timing variations."""
        element.click()
        for char in text:
            delay = random.randint(*TYPING_DELAY_MS)
            element.type(char, delay=delay)
            # Pause slightly longer after punctuation
            if char in ".!?,;:":
                time.sleep(random.uniform(0.05, 0.15))


# ============================================================================
# CLI Commands
# ============================================================================

def cmd_waves(client: ThoughtfulClient):
    """List available waves."""
    print("Fetching waves from Thoughtful...\n")
    waves = client.list_waves()

    if not waves:
        print("No waves found.")
        print("Create waves in Thoughtful (pages named 'Outreach — Wave N')")
        return

    print("Available waves:")
    print("-" * 60)
    for w in waves:
        print(f"  {w['slug']:<35} {w.get('title', '')[:30]}")
    print()
    print("Commands:")
    print("  python sender.py preview <slug>       # See contacts & messages")
    print("  python sender.py send <slug> --dry-run   # Test without sending")
    print("  python sender.py send <slug>          # Send messages")


def cmd_preview(client: ThoughtfulClient, wave_slug: str):
    """Preview contacts and messages in a wave."""
    print(f"Fetching: {wave_slug}...\n")

    try:
        wave = client.get_wave(wave_slug)
    except requests.HTTPError:
        print(f"Error: Wave '{wave_slug}' not found. Run 'python sender.py waves' to see available.")
        return

    print(f"Wave: {wave.title}")
    print(f"Contacts: {len(wave.contacts)}")
    print("=" * 60)

    for c in wave.contacts:
        print(f"\n#{c.number}. {c.name} — {c.company}")
        print(f"    {c.linkedin_url}")
        if c.signal:
            sig_preview = c.signal[:80] + "..." if len(c.signal) > 80 else c.signal
            print(f"    Signal: {sig_preview}")
        print(f"    Message:")
        # Word wrap
        words = c.message.split()
        line = "      "
        for word in words:
            if len(line) + len(word) + 1 > 80:
                print(line)
                line = "      " + word
            else:
                line = line + " " + word if line.strip() else "      " + word
        if line.strip():
            print(line)

    print("\n" + "=" * 60)
    print(f"\nTo send: python sender.py send {wave_slug} --dry-run")


def cmd_send(client: ThoughtfulClient, wave_slug: str, dry_run: bool, start: int, headless: bool, keep_open: bool = False, auto_yes: bool = False, no_send: bool = False, batch: int = 0, approve_mode: bool = False, account_name: str = None):
    """Send messages for a wave."""
    # Determine which account we're using for logging
    is_thoughtful_account = account_name and account_name.lower() == "thoughtful"
    print(f"Fetching: {wave_slug}...\n")

    try:
        wave = client.get_wave(wave_slug)
    except requests.HTTPError:
        print(f"Error: Wave '{wave_slug}' not found.")
        return

    contacts = [c for c in wave.contacts if c.number >= start]

    # Limit to batch size if specified
    if batch > 0:
        contacts = contacts[:batch]

    if not contacts:
        print(f"No contacts starting from #{start}")
        return

    print(f"Wave: {wave.title}")
    print(f"Contacts: {len(contacts)} (#{start} onwards)")
    if dry_run:
        mode = "DRY RUN"
    elif no_send:
        mode = "PREP (no send)"
    elif approve_mode:
        mode = "APPROVE (you confirm each send)"
    else:
        mode = "LIVE"
    print(f"Mode: {mode}")
    print("=" * 60)

    if not dry_run and not auto_yes and not no_send and not approve_mode:
        print("\nThis will send REAL LinkedIn messages.")
        print("Each message takes 45-90 seconds (anti-detection delays).")
        confirm = input("Type 'send' to continue: ")
        if confirm.lower() != "send":
            print("Aborted.")
            return

    sender = LinkedInSender(headless=headless)

    try:
        sender.start()

        sent = 0
        failed = 0
        failed_contacts = []
        sent_contact_objs = []  # Track contacts we actually sent to

        skipped = 0
        for i, contact in enumerate(contacts):
            print(f"\n[{i+1}/{len(contacts)}] {contact.name} ({contact.company})")

            success, status, action = sender.send_message(contact, dry_run=dry_run, no_send=no_send, approve_mode=approve_mode)

            if action == "quit":
                print(f"    ✗ {status}")
                print("\nQuitting early by user request.")
                break

            if action == "skipped":
                print(f"    ⏭ {status}")
                skipped += 1
            elif success:
                print(f"    ✓ {status}")
                sent += 1

                # Update Thoughtful (only if actually sent)
                if action == "sent":
                    sent_contact_objs.append(contact)
                    if contact.slug:
                        if client.mark_contacted(contact.slug):
                            print(f"    ✓ Thoughtful updated")
            else:
                print(f"    ✗ {status}")
                failed += 1
                failed_contacts.append((contact.number, contact.name, status))

            # Delay between messages
            if i < len(contacts) - 1:
                if dry_run or no_send or approve_mode:
                    time.sleep(2)  # Short delay in these modes
                else:
                    delay = random.uniform(*DELAY_BETWEEN_MESSAGES)
                    print(f"    Waiting {delay:.0f}s...")
                    time.sleep(delay)

        print("\n" + "=" * 60)
        if no_send:
            print(f"Done! Prepared: {sent}, Failed: {failed}")
            print("\nMessages are ready - review each tab and click Send!")
            print("\n" + "-" * 60)
            print("CONTACTS PREPARED:")
            for c in contacts:
                print(f"  #{c.number}: {c.name} ({c.company})")
            print("-" * 60)
            print("\nWhen done sending, enter the numbers you SENT (space-separated)")
            print("Example: 1 2 4 5")
            print("Or press Enter to skip tracking.\n")

            try:
                sent_input = input("Sent: ").strip()
                if sent_input:
                    sent_numbers = [int(x) for x in sent_input.split()]
                    updated = 0
                    sent_contact_objs = []
                    for c in contacts:
                        if c.number in sent_numbers:
                            sent_contact_objs.append(c)
                            if is_thoughtful_account:
                                # Thoughtful.app: update lead in Leads database
                                if client.update_lead_contacted(c):
                                    print(f"  ✓ #{c.number} {c.name} - Lead updated")
                                    updated += 1
                            else:
                                # WOZCODE: update contact page
                                if c.slug and client.mark_contacted(c.slug):
                                    print(f"  ✓ #{c.number} {c.name} - Thoughtful updated")
                                    updated += 1

                    print(f"\nUpdated {updated} contacts!")

                    # Update tracking
                    if sent_contact_objs:
                        if is_thoughtful_account:
                            # Thoughtful.app: log to weekly tracker
                            print("Updating Weekly Progress Tracker...")
                            if client.log_to_weekly_tracker(sent_contact_objs):
                                print(f"  ✓ Weekly tracker updated with {len(sent_contact_objs)} entries")
                            else:
                                print("  ✗ Could not update weekly tracker")
                        else:
                            # WOZCODE: update sales log
                            print("Updating Sales Log...")
                            if client.update_sales_log(sent_contact_objs):
                                print(f"  ✓ Sales Log updated with {len(sent_contact_objs)} entries")
                            else:
                                print("  ✗ Could not update Sales Log")
            except (ValueError, EOFError, KeyboardInterrupt):
                print("\nSkipped tracking update.")
        elif approve_mode:
            print(f"Done! Sent: {sent}, Skipped: {skipped}, Failed: {failed}")
            # Update tracking for approve mode
            if sent_contact_objs:
                if is_thoughtful_account:
                    print("Updating Leads & Weekly Tracker...")
                    for c in sent_contact_objs:
                        client.update_lead_contacted(c)
                    if client.log_to_weekly_tracker(sent_contact_objs):
                        print(f"  ✓ Weekly tracker updated with {len(sent_contact_objs)} entries")
                else:
                    print("Updating Sales Log...")
                    if client.update_sales_log(sent_contact_objs):
                        print(f"  ✓ Sales Log updated with {len(sent_contact_objs)} entries")
                    else:
                        print("  ✗ Could not update Sales Log")
        else:
            print(f"Done! Sent: {sent}, Failed: {failed}")
            # Update tracking for normal mode
            if sent_contact_objs:
                if is_thoughtful_account:
                    print("Updating Leads & Weekly Tracker...")
                    for c in sent_contact_objs:
                        client.update_lead_contacted(c)
                    if client.log_to_weekly_tracker(sent_contact_objs):
                        print(f"  ✓ Weekly tracker updated with {len(sent_contact_objs)} entries")
                else:
                    print("Updating Sales Log...")
                    if client.update_sales_log(sent_contact_objs):
                        print(f"  ✓ Sales Log updated with {len(sent_contact_objs)} entries")
                    else:
                        print("  ✗ Could not update Sales Log")

        if failed_contacts:
            print("\nFailed contacts:")
            for num, name, reason in failed_contacts:
                print(f"  #{num} {name}: {reason}")

    finally:
        if keep_open:
            print("\nBrowser kept open. Press Ctrl+C to exit or close browser manually.")
            try:
                # Keep the script alive so browser stays open
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nClosing...")
                sender.stop()
        else:
            sender.stop()


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="LinkedIn Outbound Sender - Messages from Thoughtful waves",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python sender.py waves                      # List waves
  python sender.py preview outreach-wave-6    # Preview wave
  python sender.py send outreach-wave-6 --dry-run
  python sender.py send outreach-wave-6 --start 3
        """
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # waves
    subparsers.add_parser("waves", help="List available waves")

    # preview
    p_preview = subparsers.add_parser("preview", help="Preview a wave's contacts and messages")
    p_preview.add_argument("wave", help="Wave slug (e.g., outreach-wave-6)")

    # send
    p_send = subparsers.add_parser("send", help="Send messages for a wave")
    p_send.add_argument("wave", help="Wave slug")
    p_send.add_argument("--dry-run", action="store_true", help="Open profiles but don't send")
    p_send.add_argument("--start", type=int, default=1, help="Start from contact number")
    p_send.add_argument("--headless", action="store_true", help="Run without visible browser (risky)")
    p_send.add_argument("--keep-open", action="store_true", help="Keep browser open after finishing")
    p_send.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_send.add_argument("--no-send", action="store_true", help="Prepare messages but don't send (human reviews and clicks Send)")
    p_send.add_argument("--batch", type=int, default=0, help="Number of contacts to process (default: all)")
    p_send.add_argument("--approve", action="store_true", help="Prepare each message, wait for SPACE to send (human-in-the-loop)")

    # Global account flag
    parser.add_argument("--account", type=str, help="Account name (e.g., 'shane', 'work') or API key directly")

    args = parser.parse_args()

    # Get API key - check account flag, then env vars, then .env file
    api_key = None
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    env_keys = {}

    # Load all keys from .env file
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("THOUGHTFUL_API_KEY"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        key_name = parts[0].strip()
                        key_value = parts[1].strip().strip('"\'')
                        env_keys[key_name] = key_value

    if args.account:
        # Check if it's a full API key
        if args.account.startswith("tk_"):
            api_key = args.account
        else:
            # Look for THOUGHTFUL_API_KEY_<account> in env
            account_key_name = f"THOUGHTFUL_API_KEY_{args.account.upper()}"
            api_key = env_keys.get(account_key_name) or os.environ.get(account_key_name)
            if not api_key:
                print(f"Error: Account '{args.account}' not found.")
                print(f"Add to .env: {account_key_name}=tk_live_...")
                print(f"\nAvailable accounts:")
                for k in env_keys:
                    if k.startswith("THOUGHTFUL_API_KEY_"):
                        print(f"  --account {k.replace('THOUGHTFUL_API_KEY_', '').lower()}")
                sys.exit(1)
    else:
        # Default: use THOUGHTFUL_API_KEY
        api_key = os.environ.get("THOUGHTFUL_API_KEY") or env_keys.get("THOUGHTFUL_API_KEY")

    if not api_key:
        print("Error: No API key found.")
        print("\nOptions:")
        print("  1. Add to .env:  THOUGHTFUL_API_KEY=tk_live_...")
        print("  2. Named account: THOUGHTFUL_API_KEY_SHANE=tk_live_...")
        print("     Then use: --account shane")
        print("  3. Pass directly: --account tk_live_...")
        sys.exit(1)

    print(f"Using account: {args.account or 'default'}")

    client = ThoughtfulClient(api_key)

    if args.command == "waves":
        cmd_waves(client)
    elif args.command == "preview":
        cmd_preview(client, args.wave)
    elif args.command == "send":
        cmd_send(client, args.wave, args.dry_run, args.start, args.headless, args.keep_open, args.yes, args.no_send, args.batch, args.approve, args.account)


if __name__ == "__main__":
    main()
