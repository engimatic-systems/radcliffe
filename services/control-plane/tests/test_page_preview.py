from __future__ import annotations

import json
from email.message import Message
from pathlib import Path

import pytest
from radcliffe_control import page_preview
from radcliffe_control.page_preview import PreviewError

ARTICLE = """
<!doctype html>
<html>
  <head><title>The Bancroft Copy</title></head>
  <body>
    <nav>Account Help Subscribe Navigation Noise</nav>
    <article>
      <a href="/second-story">Second story</a>
      <a href="https://user:secret@example.test/private">Private link</a>
      <p>Abraham Lincoln wrote several copies of the Gettysburg Address.</p>
      <p>The Bancroft copy was prepared for historian George Bancroft.</p>
      <p>Its provenance distinguishes it from the other manuscript copies.</p>
      <p>Four score and seven years ago begins the preserved address.</p>
      <p>The surviving document contains the full speech in Lincoln's hand.</p>
      <p>This final sentence gives the preview a stable ending.</p>
    </article>
  </body>
</html>
"""


def test_trafilatura_preview_is_bounded_relevant_and_deduplicated() -> None:
    title, extracted = page_preview.extracted_page(ARTICLE, "https://example.test/bancroft")
    chunks = page_preview.preview_chunks(extracted + "\n" + extracted, "Bancroft provenance")

    assert title == "The Bancroft Copy"
    assert len(chunks) <= page_preview.PREVIEW_CHUNKS
    assert len("\n\n...\n\n".join(chunks)) <= page_preview.MAX_PREVIEW_CHARS
    assert sum("Bancroft copy" in chunk for chunk in chunks) == 1
    assert any("provenance" in chunk for chunk in chunks)
    assert all("Navigation Noise" not in chunk for chunk in chunks)


def test_build_preview_saves_full_page_and_returns_only_extracted_passages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = ARTICLE.encode()
    monkeypatch.setattr(
        page_preview,
        "fetch_page",
        lambda _url: (
            "https://example.test/bancroft",
            "text/html",
            ARTICLE,
            encoded,
        ),
    )

    result = page_preview.build_preview(
        "https://example.test/bancroft",
        "Bancroft Gettysburg",
        tmp_path,
    )

    assert result["untrusted"] is True
    assert result["previewChunks"] <= 5
    assert "<html>" not in str(result["preview"])
    assert "Bancroft" in str(result["preview"])
    assert tmp_path.joinpath(str(result["htmlPath"])).read_bytes() == encoded
    assert "Gettysburg Address" in tmp_path.joinpath(str(result["textPath"])).read_text()
    assert result["relatedLinkCount"] == 1
    manifests = list(tmp_path.glob("pages/*.links.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())["links"] == [
        {"label": "Second story", "locator": "https://example.test/second-story"}
    ]
    assert "relatedLinks" not in result
    assert len(json.dumps(result)) < page_preview.MAX_PREVIEW_CHARS + 1_000


def test_latest_phone_gwern_markdown_is_downloaded_and_previewed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = (
        "---\n"
        "title: Essays\n"
        "description: A categorized list of Gwern.net pages.\n"
        "...\n\n"
        "This is the website of **Gwern Branwen**. "
        "It contains writing about AI, psychology, and statistics.\n\n"
        "# Newest\n\n"
        "- [Idea: Lean scaling laws](/lean-scaling){.link-modified-recently}\n"
        '- ["Sand, Rain, Wood"](/fiction/walking){.link-modified-recently}\n'
        "- [External essay](https://example.test/essay)\n"
    )
    encoded = markdown.encode()
    requested: dict[str, object] = {}

    class MarkdownResponse:
        def __init__(self) -> None:
            self.headers = Message()
            self.headers["Content-Type"] = "text/markdown; charset=utf-8"
            self.headers["Content-Length"] = str(len(encoded))

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://gwern.net/"

        def read(self, _limit: int) -> bytes:
            return encoded

    def urlopen(request, *, timeout: int):
        requested["url"] = request.full_url
        requested["accept"] = request.get_header("Accept")
        requested["timeout"] = timeout
        return MarkdownResponse()

    monkeypatch.setattr(page_preview.urllib.request, "urlopen", urlopen)

    result = page_preview.build_preview(
        "https://gwern.net/",
        "Um, that's fine. Can you go ahead and get me a uh an article by Gwern, G-W-E-R-N.net, "
        "and uh find something interesting and read to me. Just give me the first essay that's "
        "found.",
        tmp_path,
    )

    assert requested["url"] == "https://gwern.net/"
    assert "text/markdown" in str(requested["accept"])
    assert requested["timeout"] == 45
    assert result["mediaType"] == "text/markdown"
    assert result["title"] == "Essays"
    assert "Idea: Lean scaling laws" in str(result["preview"])
    assert tmp_path.joinpath(str(result["htmlPath"])).read_bytes() == encoded
    assert tmp_path.joinpath(str(result["textPath"])).read_text() == markdown.strip()
    assert result["relatedLinkCount"] == 3
    manifest = json.loads(next(tmp_path.glob("pages/*.links.json")).read_text())
    assert manifest["links"][0] == {
        "label": "Idea: Lean scaling laws",
        "locator": "https://gwern.net/lean-scaling",
    }


def test_relevant_match_starts_the_chunk_instead_of_being_truncated_at_its_end() -> None:
    long_noise = "Background material " + "irrelevant words " * 80 + "."
    text = " ".join(
        (
            long_noise,
            long_noise,
            "The full text of the Gettysburg Address follows.",
            "Four score and seven years ago begins the address.",
            "Now we are engaged in a great civil war continues it.",
        )
    )

    chunks = page_preview.preview_chunks(text, "Gettysburg Address full text")
    relevant = next(chunk for chunk in chunks if "full text" in chunk)

    assert relevant.startswith("The full text")
    assert "Four score and seven years ago" in relevant


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "https://user:password@example.test/private",
        "not a URL",
    ],
)
def test_preview_rejects_non_web_or_credentialed_urls(value: str) -> None:
    with pytest.raises(PreviewError, match="invalid_url"):
        page_preview.normalized_public_url(value)
