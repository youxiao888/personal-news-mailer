"""Unlike upstream doctor, start Chromium to prove the installed binary works."""
import asyncio
import json


async def check():
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            version = browser.version
            await browser.close()
        return {"ok": True, "browser_started": True, "version": version}
    except Exception as exc:
        return {"ok": False, "browser_started": False, "error_type": type(exc).__name__}


if __name__ == "__main__":
    result = asyncio.run(check())
    print(json.dumps(result))
    raise SystemExit(0 if result["ok"] else 1)
