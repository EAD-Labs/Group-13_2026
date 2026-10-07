"""Convert the raw PDF text of the NCERT Science Class 7 book into the
per-chapter files that ingest.py reads.

    python prepare_textbook.py            # data/raw/ncert_science_class7.txt -> data/chapterNN.txt
    python prepare_textbook.py other.txt  # a different raw dump of the same book

The raw text is what a PDF-to-text tool produces: every page ends with a
"2018-19" footer, starts with a running header ("SCIENCE" or the chapter title
in capitals, then the page number), and lines are hard-wrapped at the column
width. This script:

- drops front matter, the index, page footers and running headers;
- splits the book into chapters using the titles in config.CHAPTERS;
- turns "1.2 PHOTOSYNTHESIS — FOOD / MAKING PROCESS IN PLANTS" into
  "## 1.2 Photosynthesis — Food Making Process in Plants";
- puts the text before a chapter's first section under "## N.0 Introduction";
- drops the end-of-chapter material (Keywords, What you have learnt,
  Exercises, Extended Learning), figure captions and the Boojho/Paheli
  question bubbles that interrupt sentences;
- rejoins wrapped lines into blank-line-separated paragraphs.

Re-run python ingest.py afterwards.
"""

import os
import re
import statistics
import sys

import config

RAW_PATH = os.path.join("data", "raw", "ncert_science_class7.txt")

PAGE_FOOTER_RE = re.compile(r"^\s*2018-19\s*$", re.MULTILINE)
SECTION_RE = re.compile(r"^(\d+)\.(\d+)\s+(.+)$")
END_MATTER_RE = re.compile(
    r"^(Keywords|What you have learnt|Exercises?|Extend(ed)? Learning\b.*)$", re.IGNORECASE
)
FIGURE_CAPTION_RE = re.compile(r"^Fig\.?\s*\d+\.\d+")
BUBBLE_RE = re.compile(
    r"^(Boojho|Paheli)\s+(wants to know|is keen to know|is wondering|wonders|is curious)"
)
BLOCK_START_RE = re.compile(r"^(Activity \d+\.\d+|Table \d+\.\d+|Did you know\?|CAUTION)", re.IGNORECASE)

SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


def normalise(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def title_case(heading):
    words = heading.lower().split()
    return " ".join(
        w if i and w in SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words)
    )


def strip_running_header(lines, header_titles):
    """Remove a leading 'SCIENCE' / 'CHAPTER TITLE' line followed by a page number."""
    if len(lines) >= 2 and lines[1].isdigit():
        if lines[0] == "SCIENCE" or normalise(lines[0]) in header_titles:
            return lines[2:]
    return lines


def find_chapter_opening(lines, title, num):
    """If `lines` opens chapter `num` (its title on 1-3 lines, then the number), return
    the index just after the number; otherwise None."""
    for title_lines in (1, 2, 3):
        if len(lines) > title_lines:
            joined = normalise(" ".join(lines[:title_lines]))
            if joined == normalise(title) and lines[title_lines] == str(num):
                return title_lines + 1
    return None


def split_chapters(raw):
    chapters = sorted(config.CHAPTERS.items(), key=lambda item: item[1]["num"])
    header_titles = {normalise(info["name"]) for _, info in chapters}

    pages = [
        [line.strip() for line in page.split("\n")]
        for page in PAGE_FOOTER_RE.split(raw)
    ]

    out = {filename: [] for filename, _ in chapters}
    current = None  # index into `chapters`
    for page in pages:
        # Keep inner blank lines (they separate bullet points) but trim the ends.
        while page and not page[0]:
            page.pop(0)
        while page and not page[-1]:
            page.pop()
        if not page:
            continue
        if current is not None and "INDEX" in page[:3]:
            break

        nxt = 0 if current is None else current + 1
        info = chapters[nxt][1] if nxt < len(chapters) else None
        # An opening page ("Heat" / "4") looks like a running header ("HEAT" / "37").
        if info is None or find_chapter_opening(page, info["name"], info["num"]) is None:
            page = strip_running_header(page, header_titles)

        if info is not None:
            # The chapter title can sit a few lines down on a shared page.
            for offset in range(min(4, len(page))):
                start = find_chapter_opening(page[offset:], info["name"], info["num"])
                if start is not None:
                    if current is not None:
                        out[chapters[current][0]].extend(page[:offset])
                    page = page[offset + start:]
                    current = nxt
                    # Drop capital: "I" / "n Class VI you learnt" -> "In Class VI you learnt"
                    if len(page) >= 2 and re.fullmatch(r"[A-Z]", page[0]) and page[1][:1].islower():
                        page = [page[0] + page[1]] + page[2:]
                    break

        if current is not None:
            out[chapters[current][0]].extend(page)

    missing = [f for f, lines in out.items() if not lines]
    if missing:
        raise ValueError(f"Could not find the start of: {', '.join(missing)}")
    return out


def split_sections(lines, chapter_num):
    """[(number, title, [body lines]), ...] — body lines keep blank lines."""
    sections = [(f"{chapter_num}.0", "Introduction", [])]
    expected = 1
    in_end_matter = False
    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip Keywords/Exercises/... but not the rest of the chapter: the PDF
        # layout sometimes places the summary box before the last section.
        if END_MATTER_RE.match(line):
            in_end_matter = True
        m = SECTION_RE.match(line)
        if (
            m
            and int(m.group(1)) == chapter_num
            and int(m.group(2)) == expected
            and not re.search(r"[a-z]", m.group(3))
        ):
            heading = [m.group(3)]
            # Headings wrap: "1.3 OTHER MODES OF NUTRITION IN" / "PLANTS"
            while i + 1 < len(lines) and re.fullmatch(r"[A-Z0-9 ,:;'’—–?!()-]+", lines[i + 1]) \
                    and re.search(r"[A-Z]{2}", lines[i + 1]):
                i += 1
                heading.append(lines[i])
            sections.append((f"{chapter_num}.{expected}", title_case(" ".join(heading)), []))
            expected += 1
            in_end_matter = False
        elif not in_end_matter:
            sections[-1][2].append(line)
        i += 1
    return [s for s in sections if any(s[2])]


def clean_lines(lines):
    out = []
    skipping_bubble = 0
    for line in lines:
        if skipping_bubble:
            skipping_bubble -= 1
            if not line or line.endswith(("?", ".", "!")):
                skipping_bubble = 0
            continue
        if BUBBLE_RE.match(line):
            skipping_bubble = 0 if line.endswith(("?", ".", "!")) else 6
            continue
        if FIGURE_CAPTION_RE.match(line) or line.isdigit():
            continue
        out.append(line)
    return out


def to_paragraphs(lines):
    """Rejoin hard-wrapped lines. A paragraph ends at a blank line, before an
    Activity/Table box, or after a short line that ends a sentence."""
    lengths = [len(l) for l in lines if len(l) > 20]
    full_width = statistics.median(lengths) if lengths else 60

    paragraphs, current = [], ""
    for line in lines:
        if not line:
            if current:
                paragraphs.append(current)
            current = ""
            continue
        if BLOCK_START_RE.match(line) and current:
            paragraphs.append(current)
            current = ""
        if not current:
            current = line
        elif current.endswith("-") and current[-2:-1].isalpha() and line[:1].islower():
            current += line
        else:
            current += " " + line
        if line.endswith((".", "?", "!", ":")) and len(line) < 0.75 * full_width:
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return [re.sub(r"\s+", " ", p).strip() for p in paragraphs if p.strip()]


def main(raw_path):
    with open(raw_path, encoding="utf-8") as f:
        raw = f.read()

    for filename, lines in split_chapters(raw).items():
        info = config.CHAPTERS[filename]
        parts = [
            f"# {config.SOURCE} — Chapter {info['num']}: {info['name']}",
            f"# Generated by prepare_textbook.py from {raw_path}. Edit the raw file, not this one.",
            "",
        ]
        sections = split_sections(lines, info["num"])
        for number, title, body in sections:
            parts.append(f"## {number} {title}")
            parts.append("")
            for paragraph in to_paragraphs(clean_lines(body)):
                parts.append(paragraph)
                parts.append("")
        path = os.path.join("data", filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(parts).rstrip() + "\n")
        print(f"{path}: {len(sections)} sections ({', '.join(s[0] for s in sections)})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else RAW_PATH)
