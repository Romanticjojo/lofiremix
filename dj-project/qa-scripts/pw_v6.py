import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required","--mute-audio"])
        page = await (await b.new_context(viewport={"width":390,"height":844})).new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        await page.goto("https://romanticjojo.com/vs-sleepdj-20edc62ee462", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        # 先点一行让 audio 加载
        await page.click("#tbl tbody tr:nth-child(12)")
        await page.wait_for_timeout(7000)
        s = await page.evaluate("""() => ({
            dur: au.duration, cur: SET[curIdx].title, t: Math.round(au.currentTime),
            badge: document.getElementById('mixBadge').textContent,
            why: document.getElementById('mixWhy').textContent.slice(0,30)
        })""")
        print("段12直点:", json.dumps(s, ensure_ascii=False))
        tec_col = await page.evaluate("() => [...document.querySelectorAll('#tbl tbody tr')].slice(0,4).map(r => r.cells[5].textContent.trim())")
        key_col = await page.evaluate("() => [...document.querySelectorAll('#tbl tbody tr')].slice(0,3).map(r => r.cells[4].textContent.trim())")
        print("技巧列:", tec_col, "调性列:", key_col)
        # 刹停交叉点: 段12 出场 1552.3+135.4-10=1677.7
        await page.evaluate("seekTo(1673)")
        await page.wait_for_timeout(6500)
        s2 = await page.evaluate("""() => ({
            t: Math.round(au.currentTime), cur: SET[curIdx].title, badge: document.getElementById('mixBadge').textContent.slice(0,9),
            aLeds: [...document.getElementById('vuA').children].filter(x=>x.className).length,
            bLeds: [...document.getElementById('vuB').children].filter(x=>x.className).length
        })""")
        print("刹停交叉:", json.dumps(s2, ensure_ascii=False))
        await page.evaluate("document.getElementById('monPanel').scrollIntoView()")
        await page.wait_for_timeout(600)
        await page.screenshot(path="/tmp/agentdj.png")
        print("JS错误:", errs[:2] if errs else "无")
        await b.close()
asyncio.run(main())
