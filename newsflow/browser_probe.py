"""Direct fresh-browser diagnostic, without solving challenges or exporting bodies."""
import asyncio

from .compat import analyse_page


async def probe(samples):
    from playwright.async_api import async_playwright
    from bpc_fetch.extract import extract_article

    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for sample in samples:
            result = {'site': sample['site'], 'url': sample['url'], 'browser_version': browser.version}
            context = await browser.new_context()
            try:
                page = await context.new_page()
                response = await page.goto(sample['url'], wait_until='domcontentloaded', timeout=20000)
                await asyncio.sleep(3)  # Give ordinary client-side rendering time to settle.
                source = await page.content()
                status = response.status if response else 0
                info = analyse_page(source, status)
                result.update(http_status=status, classification=info['classification'])
                if info['classification'] in {'candidate_text_unverified', 'partial_or_paid_unverified'}:
                    article = extract_article(info['article_html'], sample['url'])
                    result['text_chars'] = len(article['text'])
                else:
                    result['text_chars'] = 0
            except Exception as exc:
                result['error'] = type(exc).__name__
            finally:
                await context.close()
            results.append(result)
        await browser.close()
    return results

