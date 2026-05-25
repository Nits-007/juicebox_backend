"""
Juicebox AI Scraper
-------------------
Automates login, search, and data extraction from Juicebox AI.
Extracts Name and Location (City, State) from search results
and saves them to a CSV file.

Usage:
    python juicebox_scraper.py
"""

import asyncio
import csv
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# -- Configuration -------------------------------------------------------------
load_dotenv()

JUICEBOX_EMAIL = os.getenv("JUICEBOX_EMAIL")
JUICEBOX_PASSWORD = os.getenv("JUICEBOX_PASSWORD")
JUICEBOX_URL = (
    "https://app.juicebox.ai/project/UTczVRSawzfAiDzn8foC/"
    "search?search_id=G8aUmNlRxdihlx8xzABH"
)

# Timeouts (ms)
NAV_TIMEOUT = 90_000
ACTION_TIMEOUT = 30_000
PANEL_TIMEOUT = 15_000

# Default fetch limit
DEFAULT_FETCH_LIMIT = 500


# -- Helpers -------------------------------------------------------------------
def parse_location(raw_location: str) -> dict:
    """Parse a raw location string into city and state components."""
    if not raw_location:
        return {"city": "", "state": ""}
    parts = [p.strip() for p in raw_location.split(",")]
    if len(parts) >= 3:
        return {"city": parts[0], "state": parts[1]}
    elif len(parts) == 2:
        return {"city": parts[0], "state": parts[1]}
    return {"city": raw_location, "state": ""}


def generate_filename(query: str) -> str:
    """Generate a descriptive CSV filename from the search query."""
    safe = re.sub(r"[^\w\s-]", "", query)[:40].strip().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"juicebox_{safe}_{ts}.csv"


async def dismiss_popups(page):
    """Dismiss any modal/dialog/popup overlay that is blocking the main screen.

    Handles a wide variety of popup patterns:
      - Dialogs with a close button (aria-label='Close', dialog-close class, X icon)
      - Overlay backdrops that can be clicked to dismiss
      - Generic modals with dismiss/cancel/close buttons
      - Falls back to pressing Escape as a last resort
    """
    dismissed = False
    try:
        dismissed = await page.evaluate("""
            () => {
                let closed = false;

                // Strategy 1: Close buttons inside dialog/modal containers
                const closeSelectors = [
                    'button[aria-label="Close"]',
                    'button[aria-label="close"]',
                    'button[aria-label="Dismiss"]',
                    'button.dialog-close',
                    '[data-slot="dialog-content"] button[class*="close"]',
                    '[data-slot="dialog-content"] button:has(svg)',
                    '[role="dialog"] button[aria-label="Close"]',
                    '[role="dialog"] button[aria-label="close"]',
                    '[role="dialog"] button.close',
                    '.modal button.close',
                    '.modal button[aria-label="Close"]',
                    '.modal-header button',
                    'button[data-dismiss="modal"]',
                ];
                for (const sel of closeSelectors) {
                    const btn = document.querySelector(sel);
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                        closed = true;
                        break;
                    }
                }
                if (closed) return true;

                // Strategy 2: Find any visible dialog/modal and look for its
                // close-like button (X, ×, Close, Dismiss, Cancel, Got it, etc.)
                const dialogContainers = document.querySelectorAll(
                    '[role="dialog"], [data-slot="dialog-content"], ' +
                    '.modal, .modal-content, [class*="popup"], [class*="overlay"]'
                );
                for (const container of dialogContainers) {
                    if (container.offsetParent === null) continue;
                    const buttons = container.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = btn.textContent.trim().toLowerCase();
                        const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                        if (
                            text === '×' || text === 'x' || text === 'close' ||
                            text === 'dismiss' || text === 'cancel' ||
                            text === 'got it' || text === 'ok' ||
                            text === 'not now' || text === 'maybe later' ||
                            text === 'no thanks' || text === 'skip' ||
                            aria === 'close' || aria === 'dismiss'
                        ) {
                            btn.click();
                            closed = true;
                            break;
                        }
                    }
                    if (closed) break;
                }
                if (closed) return true;

                // Strategy 3: Click the overlay backdrop itself to dismiss
                const overlaySelectors = [
                    '[data-slot="dialog-overlay"]',
                    '.modal-backdrop',
                    '[class*="overlay"]',
                ];
                for (const sel of overlaySelectors) {
                    const overlay = document.querySelector(sel);
                    if (overlay && overlay.offsetParent !== null) {
                        overlay.click();
                        closed = true;
                        break;
                    }
                }
                return closed;
            }
        """)
    except Exception:
        pass

    # Strategy 4: Fallback — press Escape to close any remaining modal
    if not dismissed:
        try:
            # Check if any dialog/overlay is still visible
            has_overlay = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll(
                        '[role="dialog"], [data-slot="dialog-content"], ' +
                        '.modal.show, [data-slot="dialog-overlay"]'
                    );
                    for (const el of els) {
                        if (el.offsetParent !== null) return true;
                    }
                    return false;
                }
            """)
            if has_overlay:
                await page.keyboard.press("Escape")
                dismissed = True
        except Exception:
            pass

    if dismissed:
        print("       [POPUP] Dismissed a blocking popup/dialog.")
        await page.wait_for_timeout(1000)

    return dismissed


async def safe_click(page, selector, timeout=15000, description="element"):
    """Safely find and click an element, trying multiple strategies."""
    print(f"       -> Clicking '{description}'...")
    # Try direct locator first
    el = page.locator(selector).first
    try:
        await el.wait_for(state="visible", timeout=timeout)
        await el.click()
        return True
    except Exception:
        pass
    # Try with JavaScript click as fallback
    try:
        await page.eval_on_selector(selector, "el => el.click()")
        return True
    except Exception as e:
        print(f"       [WARN] Could not click '{description}': {e}")
        return False


# -- Core Scraper --------------------------------------------------------------
async def login(page):
    """Navigate to Juicebox and complete the login flow."""
    print("[1/4] Navigating to Juicebox...")
    await page.goto(JUICEBOX_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    await page.wait_for_timeout(4000)

    # Step 1: Click "Continue with Email" -- use JS to find the right button
    print("       -> Clicking 'Continue with Email'...")
    await page.wait_for_timeout(2000)
    try:
        # Use JavaScript to find and click the correct button
        await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.includes('Continue with Email')) {
                        btn.click();
                        return;
                    }
                }
            }
        """)
    except Exception:
        # Fallback
        await page.locator("button:has-text('Continue with Email')").first.click()
    await page.wait_for_timeout(3000)

    # Step 2: Click "Login" link
    print("       -> Clicking 'Login' link...")
    try:
        await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const text = btn.textContent.trim();
                    if (text === 'Login') {
                        btn.click();
                        return;
                    }
                }
            }
        """)
    except Exception:
        await page.locator("button:has-text('Login')").first.click()
    await page.wait_for_timeout(3000)

    # Step 3: Fill email -- use JavaScript to target the visible email input
    print("       -> Filling credentials...")
    await page.evaluate(f"""
        () => {{
            const inputs = document.querySelectorAll('input[type="email"]');
            for (const inp of inputs) {{
                if (inp.offsetParent !== null) {{
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeInputValueSetter.call(inp, '{JUICEBOX_EMAIL}');
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    break;
                }}
            }}
        }}
    """)
    await page.wait_for_timeout(500)

    # Step 4: Fill password
    await page.evaluate("""
        (pwd) => {
            const inputs = document.querySelectorAll('input[type="password"]');
            for (const inp of inputs) {
                if (inp.offsetParent !== null) {
                    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeInputValueSetter.call(inp, pwd);
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    break;
                }
            }
        }
    """, JUICEBOX_PASSWORD)
    await page.wait_for_timeout(500)

    # Step 5: Click "Continue" button
    print("       -> Clicking 'Continue'...")
    await page.evaluate("""
        () => {
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                if (btn.textContent.includes('Continue') && !btn.textContent.includes('with')) {
                    btn.click();
                    return;
                }
            }
        }
    """)

    # Wait for dashboard to load
    print("       -> Waiting for dashboard...")
    await page.wait_for_timeout(8000)

    # Dismiss any popups that appeared after login (e.g. "We revamped agents")
    await dismiss_popups(page)

    # Verify we're logged in
    current_url = page.url
    if "project" in current_url or "search" in current_url:
        print("       [OK] Login successful!")
    else:
        # Try waiting for sidebar
        try:
            await page.wait_for_selector("text=Home", timeout=30000)
            print("       [OK] Login successful!")
        except PlaywrightTimeout:
            print("       [WARN] Could not verify login, continuing anyway...")


async def perform_search(page, query: str):
    """Click 'New Search', type the query, and run the search."""
    print(f"[2/4] Performing search: '{query}'")

    # Dismiss any popups before interacting with the search UI
    await dismiss_popups(page)

    # Click "+ New search" in sidebar
    print("       -> Clicking 'New search'...")
    clicked = False
    try:
        clicked = await page.evaluate("""
            () => {
                // Try sidebar "New search" link
                const elements = document.querySelectorAll('*');
                for (const el of elements) {
                    if (el.textContent.trim() === 'New search' && el.children.length === 0) {
                        el.click();
                        return true;
                    }
                }
                // Try the top-bar "+ New Search" button
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.includes('New Search')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        """)
    except Exception:
        pass
        
    if not clicked:
        try:
            await page.locator("text=New search").first.click(timeout=8000)
        except Exception as e:
            print(f"       [WARN] Fallback click for 'New search' failed: {e}")
            
    await page.wait_for_timeout(4000)

    # Dismiss popups again just in case clicking New Search triggered one
    await dismiss_popups(page)

    # Type the search query into the textarea
    print("       -> Typing search query...")
    textarea = page.locator("textarea#filter-input").first
    try:
        await textarea.wait_for(state="visible", timeout=15000)
    except PlaywrightTimeout:
        # Fallback: any visible textarea, but dismiss popups first
        print("       [WARN] Textarea not found immediately, trying to dismiss popups...")
        await dismiss_popups(page)
        textarea = page.locator("textarea, input[placeholder*='search' i], input[type='text']").first
        await textarea.wait_for(state="visible", timeout=15000)

    await textarea.fill(query)
    await page.wait_for_timeout(1000)

    # Press Enter to submit the query
    print("       -> Submitting query (pressing Enter)...")
    await textarea.press("Enter")
    await page.wait_for_timeout(8000)

    # Click "Run Search" button
    print("       -> Clicking 'Run Search'...")
    try:
        await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.includes('Run Search')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        """)
    except Exception:
        run_btn = page.locator("button:has-text('Run Search')").first
        await run_btn.click()

    # Wait for results to load
    print("       -> Waiting for results to load...")
    await page.wait_for_timeout(10000)

    # Check if results appeared
    try:
        await page.wait_for_selector(
            "[data-testid='search-display']", timeout=NAV_TIMEOUT
        )
        print("       [OK] Search results loaded!")
    except PlaywrightTimeout:
        try:
            await page.wait_for_selector("text=Matches", timeout=30000)
            print("       [OK] Search results loaded (matches found)!")
        except PlaywrightTimeout:
            print("       [WARN] Could not confirm results, continuing...")


async def extract_profile_data(page, index: int) -> dict | None:
    """Click a profile card by index, extract Name + Location, close panel."""
    try:
        # Get all profile cards fresh each time (DOM may change after panel close)
        cards = page.locator("div[role='row'][aria-label^='Profile card for']")
        card_count = await cards.count()

        if card_count == 0:
            # Fallback selector
            cards = page.locator("div[aria-label='Search results'] > div")
            card_count = await cards.count()

        if index >= card_count:
            return None

        card = cards.nth(index)

        # Scroll the card into view
        try:
            await card.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
        except Exception:
            pass

        # Get the name from the card before clicking
        card_name = ""
        try:
            name_el = card.locator("p").first
            card_name = (await name_el.inner_text()).strip()
        except Exception:
            pass

        if not card_name:
            return None

        # Click the card/name to open profile panel
        try:
            name_el = card.locator("p").first
            await name_el.click()
        except Exception:
            await card.click()
        await page.wait_for_timeout(2500)

        # Extract name from the profile panel
        name = card_name
        try:
            panel_name = page.locator("span.font-medium.truncate").first
            await panel_name.wait_for(state="visible", timeout=8000)
            name = (await panel_name.inner_text()).strip()
        except Exception:
            pass  # Use card_name as fallback

        # Extract location from the profile panel
        location_raw = ""
        try:
            loc_el = page.locator("span[aria-label^='Location:']").first
            await loc_el.wait_for(state="visible", timeout=5000)
            aria = await loc_el.get_attribute("aria-label")
            if aria:
                location_raw = aria.replace("Location: ", "").strip()
        except Exception:
            # Fallback: try to get location text below the name
            try:
                # The location is usually the second line under the name in the panel
                loc_text = await page.evaluate("""
                    () => {
                        const spans = document.querySelectorAll('span[aria-label]');
                        for (const s of spans) {
                            const label = s.getAttribute('aria-label');
                            if (label && label.startsWith('Location:')) {
                                return label.replace('Location: ', '');
                            }
                        }
                        return '';
                    }
                """)
                location_raw = loc_text.strip()
            except Exception:
                pass

        parsed = parse_location(location_raw)

        # Close the profile panel
        try:
            # Try the X/Close button
            close_btn = page.locator("button[aria-label='Close']").first
            await close_btn.wait_for(state="visible", timeout=3000)
            await close_btn.click()
        except Exception:
            try:
                # Fallback: click the close button via JS
                await page.evaluate("""
                    () => {
                        const btn = document.querySelector('button[aria-label="Close"]');
                        if (btn) btn.click();
                    }
                """)
            except Exception:
                # Last resort: press Escape
                await page.keyboard.press("Escape")
        await page.wait_for_timeout(1500)

        return {
            "name": name,
            "city": parsed["city"],
            "state": parsed["state"],
            "full_location": location_raw,
        }

    except Exception as e:
        print(f"       [WARN] Error on profile {index}: {e}")
        # Try to close any open panel
        try:
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('button[aria-label="Close"]');
                    if (btn) btn.click();
                }
            """)
        except Exception:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
        await page.wait_for_timeout(1000)
        return None


async def scrape_current_page(
    page, page_num: int, remaining_limit: int | None = None
) -> list[dict]:
    """Extract data from all profile cards on the current results page.

    Args:
        page: Playwright page instance.
        page_num: Current page number (for logging).
        remaining_limit: Max profiles still allowed to fetch (None = unlimited).
    """
    results = []
    await page.wait_for_timeout(3000)

    # Dismiss any popups before scraping this page
    await dismiss_popups(page)

    # Count cards
    cards = page.locator("div[role='row'][aria-label^='Profile card for']")
    card_count = await cards.count()

    if card_count == 0:
        cards = page.locator("div[aria-label='Search results'] > div")
        card_count = await cards.count()

    print(f"       Found {card_count} profiles on page {page_num}")

    for i in range(card_count):
        # Stop early if we've hit the fetch limit
        if remaining_limit is not None and len(results) >= remaining_limit:
            print(f"       [LIMIT] Reached fetch limit on page {page_num}, stopping.")
            break

        data = await extract_profile_data(page, i)
        if data:
            results.append(data)
            print(
                f"       [{i+1}/{card_count}] {data['name']} -- "
                f"{data['city']}, {data['state']}"
            )
        else:
            print(f"       [{i+1}/{card_count}] Skipped (no data)")

    return results


async def scrape_all_pages(page, fetch_limit: int = DEFAULT_FETCH_LIMIT) -> list[dict]:
    """Iterate through all result pages and scrape profiles up to *fetch_limit*."""
    all_results = []
    page_num = 1

    print(f"\n       Fetch limit set to {fetch_limit} records.")

    while True:
        remaining = fetch_limit - len(all_results)
        if remaining <= 0:
            print(f"\n       [LIMIT] Reached overall fetch limit of {fetch_limit}.")
            break

        print(f"\n[3/4] Scraping page {page_num}...")
        page_results = await scrape_current_page(page, page_num, remaining)
        all_results.extend(page_results)
        print(
            f"       [OK] Page {page_num} done -- {len(page_results)} profiles "
            f"(total: {len(all_results)})"
        )

        # Stop if we've reached the limit after this page
        if len(all_results) >= fetch_limit:
            print(f"\n       [LIMIT] Reached fetch limit of {fetch_limit}. Stopping.")
            break

        # Check if "Next" button exists and is enabled
        try:
            next_disabled = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.textContent.trim() === 'Next' ||
                            btn.textContent.includes('Next')) {
                            return btn.disabled;
                        }
                    }
                    return true;  // No next button found = last page
                }
            """)

            if next_disabled:
                print("\n       [DONE] Reached last page (Next button disabled).")
                break

            # Dismiss any popup before navigating
            await dismiss_popups(page)

            # Click next page
            print(f"       -> Navigating to page {page_num + 1}...")
            await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.textContent.trim() === 'Next' ||
                            btn.textContent.includes('Next')) {
                            btn.click();
                            return;
                        }
                    }
                }
            """)
            await page.wait_for_timeout(5000)
            page_num += 1

        except Exception as e:
            print(f"\n       [DONE] No more pages ({e}).")
            break

    return all_results


def save_to_csv(data: list[dict], filename: str):
    """Save the scraped data to a CSV file."""
    # Use OUTPUT_DIR env var if set (e.g. /app/output in Docker), else script dir
    output_dir = os.environ.get(
        "OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__))
    )
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "city", "state", "full_location"]
        )
        writer.writeheader()
        writer.writerows(data)
    print(f"\n[4/4] [OK] Saved {len(data)} records to: {filepath}")
    return filepath


# -- Programmatic API ----------------------------------------------------------
async def run_scraper(
    query: str,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    headless: bool = True,
    log_fn=None,
) -> tuple[list[dict], str, str]:
    """Run the scraper programmatically (used by Streamlit frontend).

    Args:
        query: Search query string.
        fetch_limit: Maximum number of records to fetch.
        headless: Run browser in headless mode.
        log_fn: Optional callback ``fn(msg)`` for each log line.

    Returns:
        ``(data_list, csv_filepath, error_message)`` – on failure, data_list is empty and error_message is populated.
    """
    import builtins

    _original_print = builtins.print

    if log_fn:
        def _custom_print(*args, sep=" ", end="\n", **kwargs):
            msg = sep.join(str(a) for a in args)
            log_fn(msg)
            _original_print(*args, sep=sep, end=end, **kwargs)
        builtins.print = _custom_print

    try:
        if not JUICEBOX_EMAIL or not JUICEBOX_PASSWORD:
            raise ValueError(
                "JUICEBOX_EMAIL and JUICEBOX_PASSWORD must be set in .env"
            )

        filename = generate_filename(query)
        print(f"Results will be saved to: {filename}")
        print(f"Fetch limit: {fetch_limit} records\n")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                slow_mo=100,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            page.set_default_timeout(ACTION_TIMEOUT)

            try:
                await login(page)
                await perform_search(page, query)
                all_data = await scrape_all_pages(page, fetch_limit)

                filepath = ""
                if all_data:
                    filepath = save_to_csv(all_data, filename)
                else:
                    print("\n[WARN] No data was extracted.")

                return all_data, filepath, ""
            
            except PlaywrightTimeout as e:
                err_msg = f"Timeout Error: The page took too long to load or an expected element was not found."
                print(f"\n[FATAL] {err_msg}\nDetails: {e}")
                try:
                    await page.screenshot(path="error.png", full_page=True)
                    print("[INFO] Saved screenshot to error.png for debugging.")
                except Exception:
                    pass
                return [], "", err_msg
            except Exception as e:
                err_msg = f"An unexpected error occurred during scraping: {e}"
                print(f"\n[FATAL] {err_msg}")
                return [], "", err_msg

            finally:
                print("\nClosing browser...")
                await browser.close()
                
    except Exception as e:
        err_msg = str(e)
        print(f"\n[FATAL] Initialization error: {err_msg}")
        return [], "", err_msg

    finally:
        builtins.print = _original_print


# -- Main Entry Point ----------------------------------------------------------
async def main():
    """Main scraper orchestration."""
    if not JUICEBOX_EMAIL or not JUICEBOX_PASSWORD:
        print("[ERROR] JUICEBOX_EMAIL and JUICEBOX_PASSWORD must be set in .env")
        sys.exit(1)

    print("=" * 60)
    print("  Juicebox AI Scraper")
    print("=" * 60)
    query = input("\nEnter your search query: ").strip()
    if not query:
        print("[ERROR] Search query cannot be empty.")
        sys.exit(1)

    # Ask for fetch limit (default 500)
    limit_input = input(
        f"\nMax records to fetch (default {DEFAULT_FETCH_LIMIT}): "
    ).strip()
    if limit_input:
        try:
            fetch_limit = int(limit_input)
            if fetch_limit <= 0:
                raise ValueError
        except ValueError:
            print("[ERROR] Invalid limit. Must be a positive integer.")
            sys.exit(1)
    else:
        fetch_limit = DEFAULT_FETCH_LIMIT

    filename = generate_filename(query)
    print(f"\nResults will be saved to: {filename}")
    print(f"Fetch limit: {fetch_limit} records\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=200,
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        page.set_default_timeout(ACTION_TIMEOUT)

        try:
            await login(page)
            await perform_search(page, query)
            all_data = await scrape_all_pages(page, fetch_limit)

            if all_data:
                save_to_csv(all_data, filename)
            else:
                print("\n[WARN] No data was extracted. Nothing to save.")

        except Exception as e:
            print(f"\n[FATAL] Error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            print("\nClosing browser...")
            await browser.close()

    print("\n[OK] Scraper finished!")


if __name__ == "__main__":
    asyncio.run(main())
