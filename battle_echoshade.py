import asyncio
import time
from playwright.async_api import async_playwright

AGENT_PROFILE = "C:/Users/hp/litany-agent/browser-profile"
PREP_URL = "https://litany.gg/demo/crawl/sector_4/prep"

RUNS_PER_SESSION = 10
WAIT_BETWEEN_RUNS = 30 * 60

async def run_one_battle(page):
    print("\nNavigating to Rigid Sector...")
    await page.goto(PREP_URL)
    await page.wait_for_timeout(5000)

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

    try:
        await page.get_by_text("Echoshade", exact=False).first.click()
        print("Echoshade selected!")
        await page.wait_for_timeout(2000)
    except:
        print("Could not select Echoshade")

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

    for stage in range(1, 4):
        print(f"  Stage {stage}...")

        try:
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                text = await btn.inner_text()
                if "BEGIN STAGE" in text.upper():
                    await btn.click()
                    break
        except:
            pass

        await page.wait_for_timeout(10000)

        if "results" in page.url:
            print("  On results page!")
            break

        content = await page.inner_text("body")

        if "CRAWL COMPLETE" in content.upper():
            print("  CRAWL COMPLETE!")
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                text = await btn.inner_text()
                if "VIEW RESULTS" in text.upper():
                    await btn.click()
                    break
            break
        elif "VICTORY" in content.upper():
            print(f"  Stage {stage} VICTORY!")
            buttons = await page.query_selector_all("button")
            for btn in buttons:
                text = await btn.inner_text()
                if "CONTINUE" in text.upper():
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    break

            if "results" in page.url:
                print("  On results page!")
                break

            content = await page.inner_text("body")
            if "CRAWL COMPLETE" in content.upper():
                print("  CRAWL COMPLETE!")
                buttons = await page.query_selector_all("button")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "VIEW RESULTS" in text.upper():
                        await btn.click()
                        break
                break

            if stage < 3:
                await page.evaluate("window.scrollBy(0, 500)")
                await page.wait_for_timeout(1000)
                buttons = await page.query_selector_all("button")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "NEXT STAGE" in text.upper():
                        await btn.click()
                        await page.wait_for_timeout(3000)
                        break
            else:
                buttons = await page.query_selector_all("button")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "VIEW RESULTS" in text.upper():
                        await btn.click()
                        break
        elif "DEFEAT" in content.upper():
            print(f"  Stage {stage} DEFEAT!")
            break

    await page.wait_for_timeout(4000)
    content = await page.inner_text("body")
    for line in content.split("\n"):
        if "PEARL" in line.upper() and any(c.isdigit() for c in line):
            print(f"  {line.strip()}")

    try:
        buttons = await page.query_selector_all("button, a")
        for btn in buttons:
            text = await btn.inner_text()
            if "RETURN TO DASHBOARD" in text.upper():
                await btn.click()
                await page.wait_for_timeout(3000)
                break
    except:
        await page.goto("https://litany.gg/demo/dashboard")
        await page.wait_for_timeout(3000)

async def main():
    print("=" * 40)
    print("LITANY BATTLE AGENT — ECHOSHADE")
    print(f"Sector: Rigid")
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
                print(f"\nWaiting 30 minutes before next run...")
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