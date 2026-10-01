import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required","--mute-audio"])
        page = await (await b.new_context(viewport={"width":390,"height":844})).new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        await page.goto("https://romanticjojo.com/vs-sleepdj-20edc62ee462", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        st = await page.evaluate("() => ({tabs: document.querySelectorAll('.stab').length, n: SET.length, sub: document.getElementById('setSub').textContent.slice(0,16)})")
        print("线上night:", json.dumps(st, ensure_ascii=False))
        await page.click("button.stab[data-k='flight']")
        await page.wait_for_timeout(1200)
        window.scrollTo(0,0); await page.evaluate("window.scrollTo(0,0)")
        await page.wait_for_timeout(7000)
        st2 = await page.evaluate("() => ({t: Math.round(au.currentTime), cur: SET[curIdx].title, dur: Math.round(au.duration), ph: document.querySelector('#tbl tbody tr:nth-child(22) .ph').textContent})")
        print("线上flight:", json.dumps(st2, ensure_ascii=False))
        await page.screenshot(path="/tmp/tabs-online.png")
        print("JS错误:", errs[:2] if errs else "无")
        await b.close()
asyncio.run(main())
