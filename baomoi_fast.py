"""Bounded concurrent Bao Moi pagination using links actually returned by the site."""
import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright
from crawl_baomoi import START_URL, NEXT_PAGE_PATTERN, extract_article_objects, response_kind


class ListingHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.payloads, self.links, self.parts = [], set(), []
        self.in_json = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.in_json = attrs.get("type") == "application/json" or attrs.get("id") == "__NEXT_DATA__"
            self.parts = []
        if tag == "a":
            url = urljoin(START_URL, attrs.get("href", ""))
            parsed = urlparse(url)
            if parsed.hostname == "baomoi.com" and NEXT_PAGE_PATTERN.fullmatch(parsed.path):
                self.links.add(url)

    def handle_data(self, text):
        if self.in_json:
            self.parts.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_json:
            try:
                self.payloads.append(json.loads("".join(self.parts)))
            except (ValueError, TypeError):
                pass
            self.in_json = False


class ArticleStore:
    def __init__(self, start, end):
        self.start, self.end = start, end
        self.articles, self.seen_ids, self.errors = {}, set(), []
        self.reached_old = False

    def process(self, payload, source):
        new = 0
        for item in extract_article_objects(payload):
            article_id = item.get("id", item.get("contentId"))
            if article_id in self.seen_ids:
                continue
            try:
                published = datetime.fromtimestamp(item["date"], tz=timezone.utc)
                parsed = urlparse(urljoin(START_URL, item["url"]))
                if parsed.hostname != "baomoi.com" or not re.search(r"-c\d+\.epi$", parsed.path):
                    continue
                self.reached_old |= published < self.start
                if self.start <= published < self.end:
                    publisher = item.get("publisher")
                    self.articles[article_id] = {
                        "id": article_id, "title": item["title"],
                        "url": parsed._replace(fragment="").geturl(),
                        "date_raw": item["date"], "date_utc": published.isoformat(),
                        "publisher": publisher.get("name") if isinstance(publisher, dict) else None,
                        "description": item.get("description", ""), "collected_from": source,
                    }
                self.seen_ids.add(article_id)
                new += 1
            except (ValueError, OverflowError, OSError) as exc:
                self.errors.append(f"Article {article_id}: {type(exc).__name__}")
        return new


def save_result(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


async def crawl(args):
    started = time.monotonic()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    store = ArticleStore(start, end)
    queue, scheduled, visited = asyncio.Queue(), {START_URL}, set()
    queue.put_nowait(START_URL)
    result = {
        "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
        "time_basis": "Bao Moi date field", "completeness_verified": False,
        "stop_reason": "running", "workers": args.workers, "batch_count": 0,
        "page_failures": [], "page_limit_reached": False, "engine": args.engine,
    }

    def checkpoint():
        result.update(article_count=len(store.articles), errors=store.errors,
                      reached_article_older_than_window=store.reached_old,
                      visited_pages=sorted(visited), scheduled_page_count=len(scheduled),
                      elapsed_seconds=round(time.monotonic() - started, 3),
                      articles=sorted(store.articles.values(), key=lambda r: r["date_raw"], reverse=True))
        save_result(Path(args.output), result)

    def schedule(links):
        for url in sorted(links):
            if url in scheduled:
                continue
            if len(scheduled) >= args.max_pages:
                result["page_limit_reached"] = True
                continue
            scheduled.add(url)
            queue.put_nowait(url)

    checkpoint()
    try:
        async with async_playwright() as p:
            browser = None
            if args.engine == "http":
                context = await p.request.new_context(user_agent="Mozilla/5.0", timeout=20000)
            else:
                browser = await p.firefox.launch(headless=not args.headed)
                context = await browser.new_context(viewport={"width": 1280, "height": 900})

            async def route_request(route):
                if route.request.resource_type in {"image", "font", "media"}:
                    await route.abort()
                else:
                    await route.continue_()
            if browser:
                await context.route("**/*", route_request)

            async def worker(number):
                page = await context.new_page() if browser else None
                pending = asyncio.Queue()
                if page:
                    page.on("response", lambda r: pending.put_nowait(r) if response_kind(r.url) else None)

                async def consume(response):
                    kind = response_kind(response.url)
                    if response.status != 200:
                        raise RuntimeError(f"{kind}: HTTP {response.status}")
                    payload = await response.json()
                    if kind == "article_api" and payload.get("err") != 0:
                        raise RuntimeError(f"API err={payload.get('err')}")
                    result["batch_count"] += 1
                    new = store.process(payload, kind)
                    exhausted = kind == "article_api" and payload.get("data", {}).get("hasMore") is False
                    return new, exhausted

                try:
                    while True:
                        url = await queue.get()
                        try:
                            while not pending.empty():
                                pending.get_nowait()
                            if page:
                                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            else:
                                for attempt in range(3):
                                    try:
                                        response = await context.get(url)
                                    except Exception:
                                        if attempt == 2:
                                            raise
                                        await asyncio.sleep(1 + attempt)
                                        continue
                                    if response.status == 429 or response.status >= 500:
                                        retry_after = response.headers.get("retry-after", "")
                                        delay = min(float(retry_after), 15) if retry_after.isdigit() else 1 + attempt
                                        await response.dispose()
                                        if attempt == 2:
                                            raise RuntimeError("Listing temporarily unavailable after retries")
                                        await asyncio.sleep(delay)
                                        continue
                                    break
                            if not response or response.status != 200:
                                raise RuntimeError(f"Listing HTTP {response.status if response else 'missing'}")
                            if urlparse(page.url if page else response.url).hostname != "baomoi.com":
                                raise RuntimeError("Unexpected listing redirect")
                            visited.add(url)
                            html = ListingHTML()
                            html.feed(await response.text())
                            if not page:
                                await response.dispose()
                            result["batch_count"] += 1
                            new = sum(store.process(payload, "embedded_json") for payload in html.payloads)
                            if not html.payloads:
                                raise RuntimeError("Missing listing JSON; inspect page structure")
                            items = [r for payload in html.payloads for r in extract_article_objects(payload)]
                            # A single old sidebar article must never terminate pagination.
                            entirely_old = bool(items) and all(r["date"] < start.timestamp() for r in items)
                            if not entirely_old:
                                schedule(html.links)
                            idle = 0
                            while page and not entirely_old and idle < args.idle_rounds:
                                before = len(store.seen_ids)
                                await page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                                exhausted = False
                                try:
                                    response = await asyncio.wait_for(pending.get(), timeout=args.response_timeout)
                                    _, exhausted = await consume(response)
                                    while not pending.empty():
                                        _, last = await consume(pending.get_nowait())
                                        exhausted |= last
                                except asyncio.TimeoutError:
                                    pass
                                links = await page.locator('a[href*="/tin-moi/trang"]').evaluate_all("nodes => nodes.map(a => a.href)")
                                schedule(link for link in links if urlparse(link).hostname == "baomoi.com" and NEXT_PAGE_PATTERN.fullmatch(urlparse(link).path))
                                idle = 0 if len(store.seen_ids) > before else idle + 1
                                if exhausted or (links and idle):
                                    break
                                await page.mouse.wheel(0, -400)
                                await page.mouse.wheel(0, 1000)
                                await asyncio.sleep(0.15)
                            print(f"WORKER {number}: {url}; saved={len(store.articles)}", flush=True)
                            checkpoint()
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            result["page_failures"].append({"url": url, "error": str(exc)})
                            print(f"PAGE FAILED: {url}; {type(exc).__name__}", flush=True)
                            checkpoint()
                        finally:
                            queue.task_done()
                finally:
                    if page:
                        await page.close()

            workers = [asyncio.create_task(worker(i + 1)) for i in range(args.workers)]
            try:
                await asyncio.wait_for(queue.join(), timeout=max(0.1, args.max_runtime - (time.monotonic() - started)))
                result["stop_reason"] = "page_limit" if result["page_limit_reached"] else "observed_pagination_exhausted"
            except asyncio.TimeoutError:
                result["stop_reason"] = "runtime_limit"
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                if browser:
                    await browser.close()
                else:
                    await context.dispose()
    except asyncio.CancelledError:
        result["stop_reason"] = "user_interrupted"
        raise
    except Exception as exc:
        result["stop_reason"] = "error"
        store.errors.append(str(exc))
    finally:
        checkpoint()
    print(f"Saved {len(store.articles)} articles to {args.output}; stop={result['stop_reason']}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="baomoi_24h.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--engine", choices=["http", "browser"], default="http", help="HTTP reads server-rendered JSON; browser captures lazy API responses")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--max-runtime", type=float, default=900)
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--idle-rounds", type=int, default=3)
    parser.add_argument("--response-timeout", type=float, default=1.5)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or min(args.hours, args.max_runtime, args.max_pages, args.idle_rounds, args.response_timeout) <= 0:
        parser.error("workers must be 1..8; other limits must be positive")
    try:
        result = asyncio.run(crawl(args))
        if result["stop_reason"] == "error":
            raise SystemExit(1)
    except KeyboardInterrupt:
        print("Interrupted; last checkpoint preserved")


if __name__ == "__main__":
    main()
