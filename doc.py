"""
doc.py
------
An EDITABLE view of your article, so the agent can fix headings and wording in place and
save a new file while leaving your original untouched.

  .docx  edited in place with python-docx: images, tables, hyperlinks and untouched paragraphs keep their formatting
  .html  edited in place with BeautifulSoup
  .md    rebuilt block by block (untouched blocks keep their original text)
  .txt   treated like markdown; the optimized copy is saved as .md

Every block is one of: title (the H1), h (a subheading, level 2-6), p (a paragraph), li (a list item), table, other.
Paragraphs that contain an image or a hyperlink are `protected`: the agent never rewrites them, so nothing is lost.
Headings that contain a link are protected too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TOC_RE = re.compile(r"^\s*(table of contents|contents|in this (guide|article|post))\s*:?\s*$", re.I)


@dataclass
class Block:
    idx: int
    kind: str                                   # title | h | p | li | table | other
    level: int = 0                              # headings: 1 (title) to 6
    text: str = ""
    protected: bool = False
    ref: object = None
    raw: str = ""                               # markdown only: original lines
    new_text: Optional[str] = None
    new_level: Optional[int] = None
    insert_before: list = field(default_factory=list)      # [(level, text)] headings added above this block

    @property
    def cur_text(self) -> str:
        return self.new_text if self.new_text is not None else self.text

    @property
    def cur_level(self) -> int:
        return self.new_level if self.new_level is not None else self.level


@dataclass
class Doc:
    path: Path
    fmt: str
    blocks: list = field(default_factory=list)
    handle: object = None                       # python-docx Document | BeautifulSoup | None
    title_style: str = "Heading 1"              # docx: "Title" when the file uses the Title style

    # -------- read helpers
    def headings(self) -> list:
        return [b for b in self.blocks if b.kind in ("title", "h")]

    def title_block(self) -> Optional[Block]:
        return next((b for b in self.blocks if b.kind == "title"), None)

    def body_blocks(self) -> list:
        return [b for b in self.blocks if b.kind != "title"]

    def body_text(self) -> str:
        parts = []
        for b in self.blocks:
            if b.kind == "title":
                continue
            for lvl, t in b.insert_before:
                parts.append(t)
            parts.append(b.cur_text)
        return "\n".join(p for p in parts if p)

    def outline(self) -> list:
        """[(level, text)] of the CURRENT heading structure, including headings that will be inserted."""
        out = []
        for b in self.blocks:
            for lvl, t in b.insert_before:
                out.append((lvl, t))
            if b.kind == "title":
                out.append((1, b.cur_text))
            elif b.kind == "h":
                out.append((b.cur_level, b.cur_text))
        return out


def _clean(t) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


# ------------------------------------------------------------------ load
def load(path) -> Doc:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".docx":
        return _load_docx(path)
    if ext in (".html", ".htm"):
        return _load_html(path)
    if ext in (".md", ".markdown", ".txt"):
        return _load_md(path)
    raise ValueError(f"Unsupported file type: {path.name} (use .docx, .html, .md or .txt)")


def _load_docx(path: Path) -> Doc:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = Document(str(path))
    doc = Doc(path=path, fmt="docx", handle=d)
    have_title = False
    for child in d.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "tbl":
            t = Table(child, d)
            cells = [" ".join(_clean(c.text) for c in row.cells) for row in t.rows]
            doc.blocks.append(Block(len(doc.blocks), "table", text=" ".join(cells), protected=True, ref=t))
            continue
        if tag != "p":
            continue
        p = Paragraph(child, d)
        text = _clean(p.text)
        style = (p.style.name if p.style is not None else "") or ""
        has_media = bool(child.xpath(".//w:drawing")) or bool(child.xpath(".//w:hyperlink"))
        if not text:
            if has_media:
                doc.blocks.append(Block(len(doc.blocks), "other", text="", protected=True, ref=p))
            continue
        m = re.match(r"Heading (\d)", style)
        if style == "Title" and not have_title:
            have_title = True
            doc.title_style = "Title"
            doc.blocks.append(Block(len(doc.blocks), "title", 1, text, protected=has_media, ref=p))
        elif m:
            lvl = int(m.group(1))
            if lvl == 1 and not have_title:
                have_title = True
                doc.title_style = "Heading 1"
                doc.blocks.append(Block(len(doc.blocks), "title", 1, text, protected=has_media, ref=p))
            else:
                doc.blocks.append(Block(len(doc.blocks), "h", max(lvl, 1), text, protected=has_media, ref=p))
        elif style.startswith("List"):
            doc.blocks.append(Block(len(doc.blocks), "li", text=text, protected=has_media, ref=p))
        else:
            doc.blocks.append(Block(len(doc.blocks), "p", text=text, protected=has_media, ref=p))
    return doc


def _load_html(path: Path) -> Doc:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    doc = Doc(path=path, fmt="html", handle=soup)
    root = soup.find("article") or soup.find("main") or soup.body or soup
    have_title = False
    for el in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table"]):
        if el.name != "table" and el.find_parent(["table", "nav", "footer", "aside", "script", "style"]):
            continue
        if el.name == "p" and el.find_parent("li"):
            continue
        text = _clean(el.get_text(" "))
        media = bool(el.find(["a", "img", "video", "iframe", "picture"]))
        if el.name == "table":
            doc.blocks.append(Block(len(doc.blocks), "table", text=text, protected=True, ref=el))
        elif not text:
            continue
        elif el.name == "h1" and not have_title:
            have_title = True
            doc.blocks.append(Block(len(doc.blocks), "title", 1, text, protected=media, ref=el))
        elif el.name[0] == "h" and el.name[1:].isdigit():
            doc.blocks.append(Block(len(doc.blocks), "h", int(el.name[1]), text, protected=media, ref=el))
        elif el.name == "li":
            doc.blocks.append(Block(len(doc.blocks), "li", text=text, protected=True if media else False, ref=el))
        else:
            doc.blocks.append(Block(len(doc.blocks), "p", text=text, protected=media, ref=el))
    return doc


def _load_md(path: Path) -> Doc:
    doc = Doc(path=path, fmt="md" if path.suffix.lower() != ".txt" else "txt")
    blocks, buf = [], []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip():
            buf.append(line)
        elif buf:
            blocks.append(buf)
            buf = []
    if buf:
        blocks.append(buf)
    have_title = False
    for n, blk in enumerate(blocks):
        raw = "\n".join(blk)
        first = blk[0].strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", first)
        if m and len(blk) == 1:
            lvl, txt = len(m.group(1)), _clean(m.group(2))
            if lvl == 1 and not have_title:
                have_title = True
                doc.blocks.append(Block(len(doc.blocks), "title", 1, txt, raw=raw))
            else:
                doc.blocks.append(Block(len(doc.blocks), "h", max(lvl, 1), txt, raw=raw))
            continue
        joined = _clean(" ".join(blk))
        media = bool(re.search(r"!\[[^\]]*\]\([^)]*\)|\[[^\]]+\]\([^)]+\)|<[a-z][^>]*>", joined))
        if first.startswith("|"):
            doc.blocks.append(Block(len(doc.blocks), "table", text=joined, protected=True, raw=raw))
        elif re.match(r"^([-*+]|\d+\.)\s", first):
            doc.blocks.append(Block(len(doc.blocks), "li", text=joined, protected=True, raw=raw))
        elif path.suffix.lower() == ".txt" and n == 0 and not have_title and len(joined.split()) <= 20:
            have_title = True
            doc.blocks.append(Block(len(doc.blocks), "title", 1, joined, raw=raw))
        else:
            doc.blocks.append(Block(len(doc.blocks), "p", text=joined, protected=media, raw=raw))
    return doc


# ------------------------------------------------------------------ save
def save(doc: Doc, out_dir: Path, stem: str) -> Path:
    """Write the edited copy next to the other outputs and return its path. The original file is never touched."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if doc.fmt == "docx":
        return _save_docx(doc, out_dir / f"{stem}.docx")
    if doc.fmt == "html":
        return _save_html(doc, out_dir / f"{stem}.html")
    return _save_md(doc, out_dir / f"{stem}.md")


def _set_par_text(p, text: str) -> None:
    runs = p.runs
    if runs:
        runs[0].text = text
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
    else:
        p.add_run(text)


def _style(d, name: str, fallback: str = "Normal"):
    try:
        return d.styles[name]
    except KeyError:
        return d.styles[fallback]


def _save_docx(doc: Doc, out: Path) -> Path:
    d = doc.handle
    for b in doc.blocks:
        if b.kind == "table":
            continue
        p = b.ref
        for lvl, text in b.insert_before:
            new = p.insert_paragraph_before(text, style=_style(d, f"Heading {lvl}"))
            _ = new
        if b.kind in ("title", "h") and not b.protected:
            if b.new_text is not None:
                _set_par_text(p, b.new_text)
            if b.kind == "title":
                p.style = _style(d, doc.title_style, "Heading 1")
            elif b.new_level is not None:
                p.style = _style(d, f"Heading {b.new_level}")
        elif b.kind in ("p", "li") and b.new_text is not None and not b.protected:
            _set_par_text(p, b.new_text)
    d.save(str(out))
    return out


def _save_html(doc: Doc, out: Path) -> Path:
    soup = doc.handle
    for b in doc.blocks:
        el = b.ref
        if b.kind == "table":
            continue
        for lvl, text in b.insert_before:
            new = soup.new_tag(f"h{lvl}")
            new.string = text
            el.insert_before(new)
        if b.protected:
            continue
        if b.new_text is not None:
            el.clear()
            el.append(b.new_text)
        if b.kind == "h" and b.new_level is not None:
            el.name = f"h{b.new_level}"
    out.write_text(str(soup), encoding="utf-8")
    return out


def _save_md(doc: Doc, out: Path) -> Path:
    parts = []
    title = doc.title_block()
    if title is None:
        pass
    for b in doc.blocks:
        for lvl, text in b.insert_before:
            parts.append("#" * lvl + " " + text)
        if b.kind == "title":
            parts.append("# " + b.cur_text)
        elif b.kind == "h":
            parts.append("#" * b.cur_level + " " + b.cur_text)
        elif b.new_text is not None and not b.protected:
            parts.append(b.new_text)
        else:
            parts.append(b.raw or b.text)
    out.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return out
