from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.rag.policy_parser import parse_policy_directory  # noqa: E402

POLICY_DIRECTORY = PROJECT_ROOT / "data" / "policies"

MARKDOWN_HEADING_PATTERN = re.compile(
    r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$"
)

ARTICLE_PATTERN = re.compile(
    r"^\s*"
    r"(?P<heading>#{1,6}\s*)?"
    r"(?:\*\*)?"
    r"(?P<label>第[一二三四五六七八九十百千零〇两0-9]+条)"
    r"(?:\*\*)?"
    r"(?P<rest>.*?)"
    r"\s*$"
)

CHAPTER_PATTERN = re.compile(
    r"^\s*"
    r"(?P<heading>#{1,6}\s*)?"
    r"(?:\*\*)?"
    r"(?P<label>第[一二三四五六七八九十百千零〇两0-9]+章)"
    r"(?:\*\*)?"
    r"(?P<rest>.*?)"
    r"\s*$"
)


def shorten(text: str, limit: int = 90) -> str:
    normalized = " ".join(text.split())

    if len(normalized) <= limit:
        return normalized

    return normalized[: limit - 3] + "..."


def inspect_document(content: str) -> dict[str, object]:
    lines = content.splitlines()

    headings: list[tuple[int, str]] = []
    articles: list[dict[str, object]] = []
    chapters: list[str] = []
    article_styles: Counter[str] = Counter()

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()

        if not stripped:
            continue

        heading_match = MARKDOWN_HEADING_PATTERN.match(stripped)

        if heading_match:
            headings.append(
                (
                    len(heading_match.group("marks")),
                    heading_match.group("title").strip(),
                )
            )

        article_match = ARTICLE_PATTERN.match(stripped)

        if article_match:
            style = (
                "markdown_heading"
                if article_match.group("heading")
                else "plain_text"
            )

            article_styles[style] += 1

            articles.append(
                {
                    "line_number": line_number,
                    "label": article_match.group("label"),
                    "style": style,
                    "line": stripped,
                }
            )

        chapter_match = CHAPTER_PATTERN.match(stripped)

        if chapter_match:
            chapters.append(stripped)

    return {
        "line_count": len(lines),
        "heading_count": len(headings),
        "article_count": len(articles),
        "chapter_count": len(chapters),
        "heading_levels": Counter(
            level
            for level, _ in headings
        ),
        "article_styles": article_styles,
        "articles": articles,
        "chapters": chapters,
    }


def main() -> int:
    documents = parse_policy_directory(POLICY_DIRECTORY)

    total_articles = 0
    documents_without_articles: list[str] = []

    print("=" * 72)
    print("企业制度结构检查")
    print(f"制度目录：{POLICY_DIRECTORY.resolve()}")
    print(f"制度数量：{len(documents)}")
    print("=" * 72)

    for document in documents:
        result = inspect_document(document.content)

        article_count = int(result["article_count"])
        total_articles += article_count

        if article_count == 0:
            documents_without_articles.append(
                document.metadata.document_id
            )

        print()
        print("-" * 72)
        print(f"文件：{document.source_path.name}")
        print(f"制度编号：{document.metadata.document_id}")
        print(f"标题：{document.metadata.title}")
        print(f"正文行数：{result['line_count']}")
        print(f"Markdown 标题数：{result['heading_count']}")
        print(f"章节标记数：{result['chapter_count']}")
        print(f"条款标记数：{article_count}")
        print(
            "标题级别分布："
            f"{dict(result['heading_levels'])}"
        )
        print(
            "条款格式分布："
            f"{dict(result['article_styles'])}"
        )

        chapters = result["chapters"]

        if chapters:
            print("章节示例：")

            for chapter in chapters[:5]:
                print(f"  - {shorten(str(chapter))}")

        articles = result["articles"]

        if articles:
            print("前 5 个条款示例：")

            for article in articles[:5]:
                print(
                    f"  - 第 {article['line_number']} 行"
                    f" | {article['style']}"
                    f" | {shorten(str(article['line']))}"
                )

    print()
    print("=" * 72)
    print("汇总")
    print(f"制度数量：{len(documents)}")
    print(f"识别到的条款总数：{total_articles}")

    if documents_without_articles:
        print(
            "未识别到条款的制度："
            + ", ".join(documents_without_articles)
        )
        print("结构检查未通过。")
        return 1

    print("全部制度均识别到条款。")
    print("结构检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
