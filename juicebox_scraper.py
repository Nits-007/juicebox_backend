"""
Juicebox AI Scraper
-------------------
Automates login, search, criteria filtering, and data extraction from Juicebox AI.
Extracts Name, LinkedIn URL, Location, and Match Percentage from
table view results and saves them to a CSV file.

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
def generate_filename(query: str) -> str:
    """Generate a descriptive CSV filename from the search query."""
    safe = re.sub(r"[^\w\s-]", "", query)[:40].strip().replace(" ", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"juicebox_{safe}_{ts}.csv"


async def dismiss_popups(page):
    """Dismiss any modal/dialog/popup overlay that is blocking the main screen."""
    dismissed = False
    try:
        dismissed = await page.evaluate("""
            () => {
                let closed = false;
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

    if not dismissed:
        try:
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
    el = page.locator(selector).first
    try:
        await el.wait_for(state="visible", timeout=timeout)
        await el.click()
        return True
    except Exception:
        pass
    try:
        await page.eval_on_selector(selector, "el => el.click()")
        return True
    except Exception as e:
        print(f"       [WARN] Could not click '{description}': {e}")
        return False


# -- Core Scraper --------------------------------------------------------------
async def login(page):
    """Navigate to Juicebox and complete the login flow."""
    print("[1/5] Navigating to Juicebox...")
    await page.goto(JUICEBOX_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    await page.wait_for_timeout(4000)

    print("       -> Clicking 'Continue with Email'...")
    await page.wait_for_timeout(2000)
    try:
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
        await page.locator("button:has-text('Continue with Email')").first.click()
    await page.wait_for_timeout(3000)

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

    print("       -> Waiting for dashboard...")
    await page.wait_for_timeout(8000)
    await dismiss_popups(page)

    current_url = page.url
    if "project" in current_url or "search" in current_url:
        print("       [OK] Login successful!")
    else:
        try:
            await page.wait_for_selector("text=Home", timeout=30000)
            print("       [OK] Login successful!")
        except PlaywrightTimeout:
            print("       [WARN] Could not verify login, continuing anyway...")


async def perform_search(page, query: str):
    """Click 'New Search', type the query, and run the search."""
    print(f"[2/5] Performing search: '{query}'")

    await dismiss_popups(page)

    print("       -> Clicking 'New search'...")
    clicked = False
    try:
        clicked = await page.evaluate("""
            () => {
                const elements = document.querySelectorAll('*');
                for (const el of elements) {
                    if (el.textContent.trim() === 'New search' && el.children.length === 0) {
                        el.click();
                        return true;
                    }
                }
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
    await dismiss_popups(page)

    print("       -> Typing search query...")
    textarea = page.locator("textarea#filter-input").first
    try:
        await textarea.wait_for(state="visible", timeout=15000)
    except PlaywrightTimeout:
        print("       [WARN] Textarea not found immediately, trying to dismiss popups...")
        await dismiss_popups(page)
        textarea = page.locator("textarea, input[placeholder*='search' i], input[type='text']").first
        await textarea.wait_for(state="visible", timeout=15000)

    await textarea.fill(query)
    await page.wait_for_timeout(1000)

    print("       -> Submitting query (pressing Enter)...")
    await textarea.press("Enter")
    await page.wait_for_timeout(8000)

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

    print("       -> Waiting for results to load...")
    await page.wait_for_timeout(10000)

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


# -- Criteria & Table View -----------------------------------------------------
async def add_criteria(page, criteria_text: str):
    """Click Criteria button, add a custom criterion, type text, and click Update."""
    print(f"[3/5] Adding criteria: '{criteria_text}'")

    await dismiss_popups(page)

    # Click the "Criteria" button
    print("       -> Clicking 'Criteria' button...")
    try:
        clicked = await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.trim().includes('Criteria')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if not clicked:
            raise Exception("Not found via JS")
    except Exception:
        await page.locator("button:has-text('Criteria')").first.click()
    await page.wait_for_timeout(3000)

    # Click "+ Add Criterion"
    print("       -> Clicking '+ Add Criterion'...")
    try:
        clicked = await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.includes('Add Criterion')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if not clicked:
            raise Exception("Not found via JS")
    except Exception:
        await page.locator("button:has-text('Add Criterion')").first.click()
    await page.wait_for_timeout(2000)

    # Type criteria text into the textarea
    print(f"       -> Typing criteria text...")
    try:
        textarea = page.locator("textarea:visible").first
        await textarea.wait_for(state="visible", timeout=10000)
        # Clear the field first
        await textarea.fill("")
        await page.wait_for_timeout(500)
        # Use press_sequentially so React registers the keystrokes properly
        await textarea.press_sequentially(criteria_text, delay=50)
    except Exception as e:
        print(f"       [WARN] Could not find criteria textarea: {e}")
        
    await page.wait_for_timeout(1500)

    # Click "Update" button
    print("       -> Clicking 'Update'...")
    try:
        update_btn = page.locator("button:has-text('Update'):visible").first
        await update_btn.wait_for(state="visible", timeout=5000)
        await update_btn.click()
    except Exception:
        try:
            clicked = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = (btn.innerText || btn.textContent || '').toLowerCase();
                        if (text.includes('update') && btn.offsetParent !== null) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            if not clicked:
                raise Exception("Not found via JS")
        except Exception as e:
            print(f"       [WARN] Failed to click 'Update' button: {e}")

    print("       -> Waiting for results to refresh with criteria...")
    await page.wait_for_timeout(15000)
    print("       [OK] Criteria applied!")


async def switch_to_table_view(page):
    """Switch the results display to Table View."""
    print("       -> Switching to Table View...")
    try:
        btn = page.locator("button[value='stack']").first
        await btn.wait_for(state="visible", timeout=10000)
        await btn.click()
    except Exception:
        try:
            btn = page.locator("button[aria-label='Table View']").first
            await btn.click(timeout=5000)
        except Exception:
            await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const label = btn.getAttribute('aria-label') || '';
                        const value = btn.getAttribute('value') || '';
                        if (label === 'Table View' || value === 'stack' || label.toLowerCase().includes('table')) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
    await page.wait_for_timeout(5000)
    print("       [OK] Switched to Table View!")


async def extract_visible_rows(page) -> list[dict]:
    """Extract data from all currently visible rows in the MUI DataGrid."""
    return await page.evaluate(r"""
        () => {
            const results = [];
            // Try MuiDataGrid-row first, fallback to any div with role="row" inside a grid
            let rows = document.querySelectorAll('.MuiDataGrid-row');
            if (rows.length === 0) {
                rows = Array.from(document.querySelectorAll('div[role="row"]')).filter(r => {
                    const rowId = r.getAttribute('data-id') || r.getAttribute('data-rowindex');
                    return rowId !== null;
                });
            }

            for (const row of rows) {
                const rowId = row.getAttribute('data-id') || row.getAttribute('data-rowindex') || '';

                // Name
                let name = '';
                const nameCell = row.querySelector('[data-field="full_name"]');
                if (nameCell) {
                    name = nameCell.textContent.trim();
                } else {
                    // Fallback Name: first column text
                    const firstCell = row.querySelector('[aria-colindex="1"]');
                    if (firstCell) name = firstCell.textContent.trim();
                }

                // LinkedIn URL
                let linkedinUrl = '';
                const profilesCell = row.querySelector('[data-field="profiles"]');
                if (profilesCell) {
                    const link = profilesCell.querySelector('a[aria-label="linkedin"], a[href*="linkedin.com"]');
                    if (link) linkedinUrl = link.href || '';
                } else {
                     // Fallback
                     const link = row.querySelector('a[href*="linkedin.com"]');
                     if (link) linkedinUrl = link.href || '';
                }

                // Location
                let location = '';
                const locationCell = row.querySelector('[data-field="location_info"], [data-field="location"]');
                if (locationCell) {
                    location = locationCell.textContent.trim();
                } else {
                    // Fallback: looking for city/state patterns? Hard to guess.
                }

                if (name) {
                    results.push({
                        row_id: rowId,
                        name: name,
                        linkedin_url: linkedinUrl,
                        location: location
                    });
                }
            }
            return results;
        }
    """)


async def scrape_table_view(page, fetch_limit: int = DEFAULT_FETCH_LIMIT) -> list[dict]:
    """Scroll through the DataGrid table and extract all profiles."""
    all_results = []
    seen_ids = set()
    no_new_data_count = 0
    max_no_new_data = 10

    print(f"\n[4/5] Scraping Table View (limit: {fetch_limit})...")
    
    # Wait for the DataGrid to actually render before we start scraping
    try:
        await page.wait_for_selector(".MuiDataGrid-row, [role='row']", state="visible", timeout=15000)
    except Exception:
        print("       [WARN] DataGrid rows didn't appear in time, scraping might fail.")
        
    await page.wait_for_timeout(3000)

    while len(all_results) < fetch_limit:
        rows_data = await extract_visible_rows(page)
        
        if not rows_data and len(all_results) == 0:
            # Debug: check if datagrid even exists
            row_count = await page.locator('[role="row"]').count()
            print(f"       [DEBUG] No data extracted from rows. Found {row_count} [role='row'] elements on page.")

        new_count = 0
        for row in rows_data:
            dedup_key = row.get("row_id") or row.get("name", "")
            if dedup_key and dedup_key not in seen_ids:
                seen_ids.add(dedup_key)
                result = {
                    "name": row.get("name", ""),
                    "linkedin_url": row.get("linkedin_url", ""),
                    "location": row.get("location", ""),
                }
                all_results.append(result)
                new_count += 1

                if len(all_results) >= fetch_limit:
                    break

        print(
            f"       Collected {len(all_results)} profiles so far "
            f"(+{new_count} new)..."
        )

        if new_count == 0:
            no_new_data_count += 1
            if no_new_data_count >= max_no_new_data:
                print("       [DONE] No more new data found after scrolling.")
                break
        else:
            no_new_data_count = 0

        if len(all_results) >= fetch_limit:
            print(f"       [LIMIT] Reached fetch limit of {fetch_limit}.")
            break

        # Scroll down in the DataGrid virtual scroller
        at_bottom = await page.evaluate("""
            () => {
                const scroller = document.querySelector('.MuiDataGrid-virtualScroller');
                if (!scroller) return true;
                const before = scroller.scrollTop;
                scroller.scrollBy(0, 300);
                return Math.abs(scroller.scrollTop - before) < 1;
            }
        """)

        if at_bottom:
            print("       [DONE] Reached bottom of table.")
            break

        await page.wait_for_timeout(800)

    final = all_results[:fetch_limit]
    print(f"\n       [OK] Total profiles scraped: {len(final)}")
    return final


def save_to_csv(data: list[dict], filename: str):
    """Save the scraped data to a CSV file."""
    output_dir = os.environ.get(
        "OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__))
    )
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "linkedin_url", "location"]
        )
        writer.writeheader()
        writer.writerows(data)
    print(f"\n[5/5] [OK] Saved {len(data)} records to: {filepath}")
    return filepath


# -- Programmatic API ----------------------------------------------------------
async def run_scraper(
    query: str,
    criteria: str = "",
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    headless: bool = True,
    save_to_disk: bool = True,
    log_fn=None,
) -> tuple[list[dict], str, str]:
    """Run the scraper programmatically.

    Returns:
        ``(data_list, csv_filepath, error_message)``
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
        print(f"Fetch limit: {fetch_limit} records")
        if criteria:
            print(f"Criteria: {criteria}\n")
        else:
            print("No criteria specified.\n")

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

                # Add criteria if provided
                if criteria:
                    await add_criteria(page, criteria)

                # Switch to table view and scrape
                await switch_to_table_view(page)
                all_data = await scrape_table_view(page, fetch_limit)

                filepath = ""
                if all_data:
                    if save_to_disk:
                        filepath = save_to_csv(all_data, filename)
                else:
                    print("\n[WARN] No data was extracted.")

                return all_data, filepath, ""

            except PlaywrightTimeout as e:
                err_msg = "Timeout Error: The page took too long to load or an expected element was not found."
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

    # Ask for criteria
    criteria = input("\nEnter criteria (or press Enter to skip): ").strip()

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
    print(f"Fetch limit: {fetch_limit} records")
    if criteria:
        print(f"Criteria: {criteria}")
    print()

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

            if criteria:
                await add_criteria(page, criteria)

            await switch_to_table_view(page)
            all_data = await scrape_table_view(page, fetch_limit)

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
