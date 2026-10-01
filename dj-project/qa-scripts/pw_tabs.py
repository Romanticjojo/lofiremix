import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required","--mute-audio"])
        page = await (await b.new_context(viewport={"width":390,"height":844})).new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        await page.goto("file:///tmp/sleep-dj.html", wait_until="domcontentloaded")
        await page.wait_for_timeout(1200)
        st = await page.evaluate("() => ({n: SET.length, src: au.src.slice(-40), sub: document.getElementById('setSub').textContent.slice(0,18), rows: document.querySelectorAll('#tbl tbody tr').length})")
        print("night:", json.dumps(st, ensure_ascii=False))
        # 切 flight
        await page.click("button.stab[data-k='flight']")
        await page.wait_for_timeout(1500)
        st2 = await page.evaluate("() => ({n: SET.length, src: au.src.slice(-44), sub: document.getElementById('setSub').textContent.slice(0,20), rows: document.querySelectorAll('#tbl tbody tr').length, ph1: document.querySelector('#tbl tbody .ph').textContent, total: Math.round(TOTAL)})")
        print("flight:", json.dumps(st2, ensure_ascii=False))
        # flight 播放测试: 点第 10 行
        await page.click("#tbl tbody tr:nth-child(10)")
        await page.wait_for_timeout(6000)
        st3 = await page.evaluate("() => ({t: Math.round(au.currentTime), cur: SET[curIdx].title, ready: au.readyState})")
        print("flight 播放:", json.dumps(st3, ensure_ascii=False))
        # 切回 night
        await page.click("button.stab[data-k='night']")
        await page.wait_for_timeout(800)
        st4 = await page.evaluate("() => ({n: SET.length, src: au.src.slice(-40), paused: au.paused})")
        print("切回night:", json.dumps(st4, ensure_ascii=False))
        await page.screenshot(path="/tmp/tabs-top.png")
        print("JS错误:", errs[:3] if errs else "无")
        await b.close()
asyncio.run(main())
