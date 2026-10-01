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
        await page.click("button.stab[data-k='flight']")
        await page.wait_for_timeout(800)
        await page.evaluate("seekTo(203)")
        # 等 readyState=4 (fragment seek 缓冲)
        for _ in range(30):
            r = await page.evaluate("au.readyState")
            if r >= 3: break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(2000)
        s = await page.evaluate("() => ({t: Math.round(au.currentTime), ready: au.readyState, badge: (document.getElementById('mixBadge')||{}).textContent ? document.getElementById('mixBadge').textContent.slice(0,9) : '-', cur: curIdx>=0 ? SET[curIdx].title : '(缓冲中)'})")
        print("flight 交叉@203:", json.dumps(s, ensure_ascii=False))
        print("JS错误:", errs[:2] if errs else "无")
        await b.close()
asyncio.run(main())
