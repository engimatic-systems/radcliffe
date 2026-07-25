from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from trafilatura import bare_extraction, html2txt

MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2 * 1024 * 1024
MAX_PREVIEW_CHARS = 3_600
MAX_CHUNK_CHARS = 680
PREVIEW_CHUNKS = 5
SENTENCES_PER_CHUNK = 3
MAX_RELATED_LINKS = 100
USER_AGENT = "RadcliffePagePreview/1.0"
MARKDOWN_LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+[^)]*)?\)")
STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "before",
        "copy",
        "download",
        "from",
        "page",
        "that",
        "their",
        "there",
        "these",
        "this",
        "with",
    }
)


class PreviewError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a" or self._href is not None:
            return
        href = next((value for name, value in attrs if name.casefold() == "href"), None)
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        self.links.append((self._href, "".join(self._text)))
        self._href = None
        self._text = []


def related_links(html: str, base_url: str) -> tuple[dict[str, str], ...]:
    parser = _LinkParser()
    parser.feed(html)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_locator, raw_label in parser.links:
        label = re.sub(r"\s+", " ", raw_label).strip()
        if not label:
            continue
        try:
            locator = normalized_public_url(urljoin(base_url, raw_locator))
        except PreviewError:
            continue
        if locator in seen:
            continue
        seen.add(locator)
        result.append({"label": label[:300], "locator": locator[:2_048]})
        if len(result) >= MAX_RELATED_LINKS:
            break
    return tuple(result)


def related_markdown_links(markdown: str, base_url: str) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in MARKDOWN_LINK.finditer(markdown):
        label = re.sub(r"<[^>]+>|[*_`]", "", match.group(1))
        label = re.sub(r"\s+", " ", label).strip().strip("\"'")
        if not label:
            continue
        try:
            locator = normalized_public_url(urljoin(base_url, match.group(2) or match.group(3)))
        except PreviewError:
            continue
        if locator in seen:
            continue
        seen.add(locator)
        result.append({"label": label[:300], "locator": locator[:2_048]})
        if len(result) >= MAX_RELATED_LINKS:
            break
    return tuple(result)


def normalized_public_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PreviewError("invalid_url")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def fetch_page(url: str, *, timeout_seconds: int = 45) -> tuple[str, str, str, bytes]:
    request = urllib.request.Request(
        normalized_public_url(url),
        headers={
            "accept": "text/html,application/xhtml+xml,text/markdown,text/plain",
            "user-agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            media_type = response.headers.get_content_type().lower()
            if media_type not in {
                "text/html",
                "application/xhtml+xml",
                "text/markdown",
                "text/plain",
            }:
                raise PreviewError("unsupported_content_type")
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > MAX_DOWNLOAD_BYTES:
                        raise PreviewError("page_too_large")
                except ValueError:
                    pass
            encoded = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(encoded) > MAX_DOWNLOAD_BYTES:
                raise PreviewError("page_too_large")
            encoding = response.headers.get_content_charset() or "utf-8"
            text = encoded.decode(encoding, errors="replace")
            return normalized_public_url(response.geturl()), media_type, text, encoded
    except PreviewError:
        raise
    except urllib.error.HTTPError as error:
        raise PreviewError(f"http_{error.code}") from error
    except (OSError, UnicodeError, urllib.error.URLError, ValueError) as error:
        raise PreviewError("fetch_failed") from error


def extracted_page(html: str, url: str) -> tuple[str | None, str]:
    document = bare_extraction(
        html,
        url=url,
        favor_precision=True,
        include_comments=False,
        include_tables=False,
        deduplicate=True,
        with_metadata=True,
    )
    title: str | None = None
    text = ""
    if document is not None:
        title_value = getattr(document, "title", None)
        text_value = getattr(document, "text", None) or getattr(document, "raw_text", None)
        title = (
            title_value.strip() if isinstance(title_value, str) and title_value.strip() else None
        )
        text = text_value if isinstance(text_value, str) else ""
    if not text.strip():
        text = html2txt(html) or ""
    normalized = re.sub(r"[\t\f\v ]+", " ", text)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        raise PreviewError("no_extractable_text")
    return title, normalized[:MAX_EXTRACTED_CHARS]


def extracted_content(content: str, url: str, media_type: str) -> tuple[str | None, str]:
    if media_type not in {"text/markdown", "text/plain"}:
        return extracted_page(content, url)
    normalized = re.sub(r"[\t\f\v ]+", " ", content)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        raise PreviewError("no_extractable_text")
    title: str | None = None
    if media_type == "text/markdown":
        frontmatter = re.match(r"\A---\s*\n(.*?)(?:\n(?:---|\.\.\.)\s*(?:\n|$))", normalized, re.S)
        frontmatter_title = (
            re.search(r"(?m)^title:\s*(.+?)\s*$", frontmatter.group(1))
            if frontmatter is not None
            else None
        )
        if frontmatter_title is not None:
            title = frontmatter_title.group(1).strip().strip("\"'")[:300] or None
        if title is None:
            heading = re.search(r"(?m)^#\s+(.+?)\s*$", normalized)
            if heading is not None:
                title = heading.group(1).strip()[:300] or None
    return title, normalized[:MAX_EXTRACTED_CHARS]


def query_terms(query: str) -> tuple[str, ...]:
    terms = {
        term.casefold()
        for term in re.findall(r"[\w'-]{3,}", query, flags=re.UNICODE)
        if term.casefold() not in STOP_WORDS
    }
    return tuple(sorted(terms))


def sentences(text: str) -> list[str]:
    values = re.split(r"(?<=[.!?])(?:[\"')\]]*)\s+|\n+", text)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        marker = normalized.casefold()
        if normalized and marker not in seen:
            result.append(normalized)
            seen.add(marker)
    return result


def _window(values: list[str], start: int) -> str:
    chunk = " ".join(values[start : start + SENTENCES_PER_CHUNK]).strip()
    if len(chunk) <= MAX_CHUNK_CHARS:
        return chunk
    return f"{chunk[: MAX_CHUNK_CHARS - 1].rstrip()}…"


def markdown_section_chunks(text: str) -> tuple[str, ...]:
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if re.match(r"^#{1,6}\s+\S", line) is None:
            continue
        section = [line]
        for candidate in lines[index + 1 :]:
            if re.match(r"^#{1,6}\s+\S", candidate):
                break
            if not candidate or candidate.startswith("<!--") or candidate in {"-->", "</div>"}:
                continue
            section.append(candidate)
            if len(section) >= SENTENCES_PER_CHUNK + 1:
                break
        if len(section) > 1:
            chunk = " ".join(section)
            return (
                chunk
                if len(chunk) <= MAX_CHUNK_CHARS
                else f"{chunk[: MAX_CHUNK_CHARS - 1].rstrip()}…",
            )
    return ()


def preview_chunks(
    text: str,
    query: str,
    *,
    count: int = PREVIEW_CHUNKS,
    preferred: tuple[str, ...] = (),
) -> tuple[str, ...]:
    values = sentences(text)
    if not values:
        return ()
    last_start = max(0, len(values) - SENTENCES_PER_CHUNK)
    selected: list[int] = []
    terms = query_terms(query)
    if terms:
        scored: list[tuple[int, int]] = []
        for start in range(last_start + 1):
            window = values[start : start + SENTENCES_PER_CHUNK]
            candidate = " ".join(window).casefold()
            first_sentence = window[0].casefold()
            score = sum(candidate.count(term) for term in terms) + 2 * sum(
                first_sentence.count(term) for term in terms
            )
            if score:
                scored.append((score, start))
        for _score, start in sorted(scored, key=lambda item: (-item[0], item[1])):
            if all(abs(start - existing) >= SENTENCES_PER_CHUNK for existing in selected):
                selected.append(start)
            if len(selected) >= min(3, count):
                break
    if count == 1:
        fallback = [0]
    else:
        fallback = [round(index * last_start / (count - 1)) for index in range(count)]
    for start in fallback:
        if all(abs(start - existing) >= SENTENCES_PER_CHUNK for existing in selected):
            selected.append(start)
        if len(selected) >= count:
            break
    for start in range(last_start + 1):
        if all(abs(start - existing) >= SENTENCES_PER_CHUNK for existing in selected):
            selected.append(start)
        if len(selected) >= count:
            break
    candidates = (*preferred, *(_window(values, start) for start in sorted(selected[:count])))
    chunks: list[str] = []
    seen_chunks: set[str] = set()
    for chunk in candidates:
        normalized = re.sub(r"\s+", " ", chunk).strip()
        marker = normalized.casefold()
        if normalized and marker not in seen_chunks:
            chunks.append(normalized)
            seen_chunks.add(marker)
        if len(chunks) >= count:
            break
    bounded: list[str] = []
    used = 0
    for chunk in chunks:
        separator = 7 if bounded else 0
        remaining = MAX_PREVIEW_CHARS - used - separator
        if remaining <= 0:
            break
        bounded.append(chunk if len(chunk) <= remaining else f"{chunk[: remaining - 1].rstrip()}…")
        used += len(bounded[-1]) + separator
    return tuple(bounded)


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)


def build_preview(url: str, query: str, workspace: Path) -> dict[str, object]:
    final_url, media_type, html, encoded = fetch_page(url)
    title, extracted = extracted_content(html, final_url, media_type)
    digest = hashlib.sha256(final_url.encode() + b"\0" + encoded).hexdigest()[:16]
    pages = workspace.resolve() / "pages"
    pages.mkdir(mode=0o700, exist_ok=True)
    html_relative = f"pages/page-{digest}.html"
    text_relative = f"pages/page-{digest}.txt"
    links_relative = f"pages/page-{digest}.links.json"
    html_path = workspace.resolve() / html_relative
    text_path = workspace.resolve() / text_relative
    links_path = workspace.resolve() / links_relative
    if not html_path.exists():
        _write_private(html_path, encoded)
    if not text_path.exists():
        _write_private(text_path, extracted.encode())
    if media_type in {"text/html", "application/xhtml+xml"}:
        links = related_links(html, final_url)
    elif media_type == "text/markdown":
        links = related_markdown_links(html, final_url)
    else:
        links = ()
    if not links_path.exists():
        _write_private(
            links_path,
            (
                json.dumps(
                    {"schemaVersion": 1, "sourceUrl": final_url, "links": links},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
        )
    chunks = preview_chunks(
        extracted,
        query,
        preferred=markdown_section_chunks(extracted) if media_type == "text/markdown" else (),
    )
    return {
        "schemaVersion": 1,
        "untrusted": True,
        "finalUrl": final_url,
        "mediaType": media_type,
        "title": title,
        "downloadedBytes": len(encoded),
        "extractedChars": len(extracted),
        "htmlPath": html_relative,
        "textPath": text_relative,
        "preview": "\n\n...\n\n".join(chunks),
        "previewChunks": len(chunks),
        "relatedLinkCount": len(links),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--url", required=True)
    value.add_argument("--query", default="")
    return value


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        result = build_preview(options.url, options.query, Path.cwd())
    except PreviewError as error:
        print(json.dumps({"schemaVersion": 1, "error": error.code}, separators=(",", ":")))
        return 1
    except (OSError, RuntimeError, ValueError):
        print(json.dumps({"schemaVersion": 1, "error": "preview_failed"}, separators=(",", ":")))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
