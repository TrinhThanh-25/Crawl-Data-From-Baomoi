"""Parallel article processing; each thread owns its own Playwright runtime."""
import argparse
import queue
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from crawl_vnexpress import (COMMENT_HOST, START, capture_initial_comments,
                            collect_comments, discover_articles, integer,
                            publication_time, save_result)


def block_heavy(route):
    if route.request.resource_type in {"image", "font", "media"}:
        route.abort()
    else:
        route.continue_()


def run(args):
    started = time.monotonic()
    deadline = started + args.max_runtime
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    result = {
        "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
        "coverage": "VnExpress tin-tuc-24h listing; all-site completeness not verified",
        "interaction_formula": "likes on all unique roots/replies + number of unique replies",
        "metrics_note": "Live snapshots; sort_by=like may change during pagination",
        "articles": [], "failures": [], "time_unknown": [], "workers": args.workers,
        "processed_candidates": 0, "stop_reason": "running",
    }
    output = Path(args.output)
    tasks, updates, stop = queue.Queue(), queue.Queue(), threading.Event()
    threads = []

    def checkpoint():
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_result(output, result)

    def worker(number):
        try:
            with sync_playwright() as p:
                browser = p.firefox.launch(headless=not args.headed)
                try:
                    context = browser.new_context(viewport={"width": 1280, "height": 900})
                    context.route("**/*", block_heavy)
                    page = context.new_page()
                    page._crawler_comment_timeout = args.comment_timeout
                    page._crawler_deadline = deadline
                    page._crawler_request_delay = args.request_delay * 1000
                    pending = deque()
                    def on_response(response):
                        parsed = urlparse(response.url)
                        if parsed.hostname == COMMENT_HOST and parsed.path == "/index/get":
                            pending.append(response)
                    page.on("response", on_response)
                    while not stop.is_set() and time.monotonic() < deadline:
                        try:
                            article = tasks.get_nowait()
                        except queue.Empty:
                            break
                        update = {"row": None, "unknown": None, "failures": []}
                        pending.clear()
                        try:
                            response = page.goto(article["url"], wait_until="domcontentloaded", timeout=30000)
                            if not response or response.status != 200:
                                raise RuntimeError("Article did not return HTTP 200")
                            published, source = publication_time(page)
                            if not published:
                                update["unknown"] = article
                            elif start <= published < end:
                                row = {**article, "published_at": published.isoformat(), "time_source": source,
                                       "comment_count": None, "comments_complete": False, "interaction_score": None}
                                update["row"] = row
                                try:
                                    template, initial = capture_initial_comments(page, pending, article["article_id"])
                                    row["comment_count"] = integer(initial["data"]["totalitem"])
                                    row.update(collect_comments(context, page, template, initial, article["article_id"]))
                                except Exception as exc:
                                    row["error"] = f"{type(exc).__name__}: comment collection failed"
                                    update["failures"].append({"article_id": article["article_id"], "stage": "comments", "error": type(exc).__name__})
                        except Exception as exc:
                            update["failures"].append({"article_id": article["article_id"], "stage": "article", "error": type(exc).__name__})
                        finally:
                            updates.put(("article", update))
                            tasks.task_done()
                finally:
                    browser.close()
        except Exception as exc:
            updates.put(("failure", {"stage": "worker", "worker": number, "error": type(exc).__name__}))
        finally:
            updates.put(("done", number))

    checkpoint()
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(headless=not args.headed)
            try:
                context = browser.new_context()
                context.route("**/*", block_heavy)
                candidates, urls, complete = discover_articles(context.new_page())
            finally:
                browser.close()
        result.update(candidate_count=len(candidates), listing_urls=urls, listing_pagination_exhausted=complete)
        selected = candidates[:args.max_articles] if args.max_articles else candidates
        for article in selected:
            tasks.put(article)
        for number in range(args.workers):
            thread = threading.Thread(target=worker, args=(number + 1,), name=f"vnexpress-{number+1}")
            thread.start()
            threads.append(thread)
        done, last_save = 0, time.monotonic()
        while done < len(threads):
            if time.monotonic() >= deadline:
                stop.set()
            try:
                kind, update = updates.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "done":
                done += 1
            elif kind == "failure":
                result["failures"].append(update)
            else:
                if update["row"] is not None:
                    result["articles"].append(update["row"])
                if update["unknown"] is not None:
                    result["time_unknown"].append(update["unknown"])
                result["failures"].extend(update["failures"])
                result["processed_candidates"] += 1
                print(f"PROGRESS {result['processed_candidates']}/{len(selected)}; saved={len(result['articles'])}", flush=True)
            if time.monotonic() - last_save >= 5:
                checkpoint()
                last_save = time.monotonic()
        if stop.is_set() and result["processed_candidates"] < len(selected):
            result["stop_reason"] = "runtime_limit"
        elif result["processed_candidates"] < len(selected):
            result["stop_reason"] = "worker_failure"
        else:
            result["stop_reason"] = "trial_limit" if len(selected) < len(candidates) else "listing_processed"
    except KeyboardInterrupt:
        result["stop_reason"] = "user_interrupted"
        stop.set()
    except Exception as exc:
        result["stop_reason"] = "error"
        result["failures"].append({"stage": "run", "error": type(exc).__name__})
        stop.set()
    finally:
        for thread in threads:
            thread.join()
        # Preserve completed work even if the coordinator was interrupted.
        while not updates.empty():
            kind, update = updates.get_nowait()
            if kind == "article":
                if update["row"] is not None:
                    result["articles"].append(update["row"])
                if update["unknown"] is not None:
                    result["time_unknown"].append(update["unknown"])
                result["failures"].extend(update["failures"])
                result["processed_candidates"] += 1
            elif kind == "failure":
                result["failures"].append(update)
        checkpoint()
    print(f"Saved {len(result['articles'])} articles to {output}; stop={result['stop_reason']}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="vnexpress_24h.json")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--max-runtime", type=float, default=900)
    parser.add_argument("--max-articles", type=int, default=0)
    parser.add_argument("--comment-timeout", type=float, default=10)
    parser.add_argument("--request-delay", type=float, default=0.15)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--headless", action="store_true", help="Compatibility option; headless is the default")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.max_articles < 0 or min(args.hours, args.max_runtime, args.comment_timeout) <= 0 or args.request_delay < 0:
        parser.error("Invalid concurrency or limits")
    result = run(args)
    if result["stop_reason"] in {"error", "worker_failure"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
