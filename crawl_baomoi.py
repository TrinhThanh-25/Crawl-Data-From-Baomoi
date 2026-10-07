import re
from urllib.parse import parse_qs, urlparse


START_URL = "https://baomoi.com/tin-moi.epi"

NEXT_PAGE_PATTERN = re.compile(r"/tin-moi/trang\d+\.epi")


def response_kind(url):
    parsed = urlparse(url)

    if (
        parsed.hostname == "w-api.baomoi.com"
        and parsed.path == "/api/v1/content/get/list-by-type"
    ):
        params = parse_qs(parsed.query)
        if params.get("listType") == ["3"]:
            return "article_api"

    # Ghi nhận JSON từ Báo Mới khi chuyển trang.
    # Cấu trúc response loại này chưa được xác minh.
    if (
        parsed.hostname == "baomoi.com"
        and parsed.path.endswith(".json")
    ):
        return "page_json"

    return None


def extract_article_objects(value):
    """Tìm object có cấu trúc bài giống JSON mẫu anh đã gửi."""
    if isinstance(value, dict):
        article_id = value.get("id", value.get("contentId"))

        if (
            isinstance(article_id, int)
            and not isinstance(article_id, bool)
            and isinstance(value.get("title"), str)
            and isinstance(value.get("date"), (int, float))
            and not isinstance(value["date"], bool)
            and isinstance(value.get("url"), str)
        ):
            yield value
            return

        for child in value.values():
            yield from extract_article_objects(child)

    elif isinstance(value, list):
        for child in value:
            yield from extract_article_objects(child)


if __name__ == "__main__":
    from baomoi_fast import main as fast_main
    fast_main()
