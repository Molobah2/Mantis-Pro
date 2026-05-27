import asyncio
import time
from playwright.async_api import async_playwright

AGENT_PROFILE = "C:/Users/hp/litany-agent/browser-profile"
PREP_URL = "https://litany.gg/demo/crawl/sector_0/prep"

RUNS_PER_SESSION = 50
WAIT_BETWEEN_RUNS = 20 * 60

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
    print("\nNavigating to Surge Sector...")
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
        await page.get_by_text("Shellvoid", exact=False).first.click()
        print("Shellvoid selected!")
        await page.wait_for_timeout(2000)
    except:
        print("Could not select Shellvoid")

    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(1000)

    try:
        buttons = await page.query_selector_all("button")
        for btn in buttons:
            text = await btn.inner_text()
            if "ENTER" in text.upper() and "SECTOR" in text.upper():
                await btn.click()
                print("Entered Surge Sector!")
                await page.wait_for_timeout(5000)
                break
    except:
        print("Could not click Enter")

    for stage in range(1, 4):
        print(f"  Stage {stage}...")

        await click_btn(page, "BEGIN STAGE")
        print("  Battle running...")
        await page.wait_for_timeout(10000)

        content = await page.inner_text("body")

        if "CRAWL COMPLETE" in content.upper():
            print("  CRAWL COMPLETE!")
            await page.wait_for_timeout(2000)
            await click_btn(page, "VIEW RESULTS")
            await page.wait_for_timeout(3000)
            break

        elif "VICTORY" in content.upper():
            print(f"  Stage {stage} VICTORY!")
            await click_btn(page, "CONTINUE")
            await page.wait_for_timeout(3000)

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
                await page.screenshot(path=f"C:/Users/hp/litany-agent/stage{stage}_debug.png")
                await click_btn(page, "NEXT STAGE")
                await page.wait_for_timeout(5000)
            else:
                await page.wait_for_timeout(3000)
                content = await page.inner_text("body")
                if "CRAWL COMPLETE" in content.upper():
                    await click_btn(page, "VIEW RESULTS")
                else:
                    await click_btn(page, "RESULT")
                await page.wait_for_timeout(3000)

        elif "DEFEAT" in content.upper():
            print(f"  Stage {stage} DEFEAT!")
            break

    await page.wait_for_timeout(4000)
    content = await page.inner_text("body")
    for line in content.split("\n"):
        if "PEARL" in line.upper() and any(c.isdigit() for c in line):
            print(f"  {line.strip()}")

    success = await click_btn(page, "RETURN TO DASHBOARD")
    if not success:
        print("  Going back to dashboard manually...")
        await page.goto("https://litany.gg/demo/dashboard")
    await page.wait_for_timeout(3000)

async def main():
    print("=" * 40)
    print("LITANY AUTO BATTLE AGENT — SHELLVOID")
    print(f"Sector: Surge")
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