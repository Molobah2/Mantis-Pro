import asyncio
import time
from playwright.async_api import async_playwright

AGENT_PROFILE = "C:/Users/hp/litany-agent/browser-profile"
PREP_URL = "https://litany.gg/demo/crawl/sector_4/prep"
RESULTS_URL = "https://litany.gg/demo/crawl/sector_4/results"

RUNS_PER_SESSION = 50
WAIT_BETWEEN_RUNS = 20 * 60

def read_skill(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return f"[{filename} not found]"

async def click_btn(page, keyword):
    try:
        buttons = await page.query_selector_all("button, a")
        for btn in buttons:
            text = await btn.inner_text()
            if keyword.upper() in text.upper():
                await btn.click()
                await page.wait_for_timeout(2000)
                return True
    except:
        pass
    return False

async def run_one_battle(page):
    print("\nNavigating to Rigid Sector...")
    await page.goto(PREP_URL)
    await page.wait_for_timeout(5000)

    # Close tutorial if present
    try:
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            text = await btn.inner_text()
            if text.strip().lower() == "x":
                await btn.click()
                await page.wait_for_timeout(1000)
                break
    except:
        pass

    # Select Primordia
    try:
        await page.get_by_text("Primordia", exact=False).first.click()
        print("Primordia selected!")
        await page.wait_for_timeout(2000)
    except:
        print("Could not select Primordia")

    # Scroll and click Enter
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(1000)

    try:
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            text = await btn.inner_text()
            if "ENTER" in text.upper() and "SECTOR" in text.upper():
                await btn.click()
                print("Entered Rigid Sector!")
                await page.wait_for_timeout(5000)
                break
    except:
        print("Could not click Enter")

    # Run 3 stages
    for stage in range(1, 4):
        print(f"  Stage {stage}...")

        await click_btn(page, "BEGIN STAGE")
        print("  Battle running...")
        await page.wait_for_timeout(10000)

        current_url = page.url
        content = await page.inner_text("body")

        # Already on results
        if "results" in current_url:
            print("  On results page!")
            break

        if "CRAWL COMPLETE" in content.upper():
            print("  CRAWL COMPLETE!")
            await page.wait_for_timeout(2000)
            await click_btn(page, "VIEW RESULTS")
            await page.wait_for_timeout(3000)
            break

        elif "VICTORY" in content.upper():
            print(f"  Stage {stage} VICTORY!")
            await click_btn(page, "CONTINUE")
            await page.wait_for_timeout(5000)

            current_url = page.url
            print(f"  URL after continue: {current_url}")

            # Already on results
            if "results" in current_url:
                print("  On results page!")
                break

            # Page navigated away unexpectedly
            if "crawl" not in current_url:
                print("  Page left crawl — going to results directly...")
                await page.goto(RESULTS_URL)
                await page.wait_for_timeout(3000)
                break

            content = await page.inner_text("body")
            if "CRAWL COMPLETE" in content.upper():
                print("  CRAWL COMPLETE!")
                await page.wait_for_timeout(2000)
                await click_btn(page, "VIEW RESULTS")
                await page.wait_for_timeout(3000)
                break

            if stage < 3:
                await page.wait_for_timeout(3000)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(1000)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
                await click_btn(page, "NEXT STAGE")
                await page.wait_for_timeout(5000)
            else:
                # Last stage — go directly to results
                print("  Last stage done — going to results...")
                await page.goto(RESULTS_URL)
                await page.wait_for_timeout(3000)
                break

        elif "DEFEAT" in content.upper():
            print(f"  Stage {stage} DEFEAT!")
            break

    # Show PEARL earned
    await page.wait_for_timeout(4000)
    content = await page.inner_text("body")
    for line in content.split("\n"):
        if "PEARL" in line.upper() and any(c.isdigit() for c in line):
            print(f"  {line.strip()}")

    # Return to dashboard
    success = await click_btn(page, "RETURN TO DASHBOARD")
    if not success:
        print("  Going back to dashboard manually...")
        await page.goto("https://litany.gg/demo/dashboard")
    await page.wait_for_timeout(3000)

async def main():
    # Load skill file
    print("=" * 40)
    print("LOADING SKILLS...")
    print("=" * 40)
    litany_skill = read_skill("LITANY_SKILL.txt")
    if "[" not in litany_skill:
        print(f"  LITANY_SKILL.txt: OK ({len(litany_skill)} characters loaded)")
    else:
        print("  LITANY_SKILL.txt: MISSING")

    print()
    print("=" * 40)
    print("LITANY AUTO BATTLE AGENT — PRIMORDIA")
    print("Sector: Rigid")
    print(f"Runs: {RUNS_PER_SESSION} total")
    print(f"Interval: every {WAIT_BETWEEN_RUNS // 60} minutes")
    print("=" * 40)

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=AGENT_PROFILE,
            headless=False,
        )

        page = await browser.new_page()
        await page.goto("https://litany.gg/demo")
        print("Waiting 15 seconds for wallet to connect...")
        await page.wait_for_timeout(15000)

        content = await page.inner_text("body")
        if "ENTER THE GAUNTLET" in content.upper():
            print("Waiting 5 seconds before clicking...")
            await page.wait_for_timeout(5000)
            try:
                buttons = await page.query_selector_all("button, a")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "ENTER THE GAUNTLET" in text.upper():
                        await btn.click()
                        print("Entered the gauntlet!")
                        await page.wait_for_timeout(5000)
                        break
            except:
                pass

        for run in range(1, RUNS_PER_SESSION + 1):
            print(f"\n{'='*40}")
            print(f"RUN {run} of {RUNS_PER_SESSION}")
            print(f"Time: {time.strftime('%H:%M:%S')}")
            print(f"{'='*40}")

            try:
                await run_one_battle(page)
                print(f"Run {run} complete!")
            except Exception as e:
                print(f"Run {run} failed: {e}")
                await page.goto("https://litany.gg/demo/dashboard")
                await page.wait_for_timeout(3000)

            if run < RUNS_PER_SESSION:
                print(f"\nWaiting 20 minutes before next run...")
                for remaining in range(WAIT_BETWEEN_RUNS, 0, -60):
                    mins = remaining // 60
                    print(f"  Next run in {mins} minutes...")
                    await asyncio.sleep(60)

        print()
        print("=" * 40)
        print("ALL RUNS COMPLETE!")
        print("=" * 40)
        input("Press ENTER to close...")
        await browser.close()

asyncio.run(main())