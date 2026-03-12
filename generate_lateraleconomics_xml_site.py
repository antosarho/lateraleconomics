#!/usr/bin/env python3

import html
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


SOURCE_XML = Path("lateraleconomics.WordPress.2026-03-03.xml")
OUTPUT_DIR = Path("xml-site")
WP_CONTENT_SOURCE = Path("site-local/wp-content")
WAYBACK_STAMP = "20260121040722"
SITE_HOSTS = {
    "lateraleconomics.com.au",
    "www.lateraleconomics.com.au",
}
NAV_SLUGS = [
    "home",
    "who-we-are",
    "outputs",
    "presentations",
    "archives",
    "clients",
    "testimonials",
    "contact-us",
]
ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
CDATA_FIELD_RE = r"<{name}><!\[CDATA\[(.*?)\]\]></{name}>"
TEXT_FIELD_RE = r"<{name}>(.*?)</{name}>"
META_RE = re.compile(
    r"<wp:postmeta>\s*<wp:meta_key><!\[CDATA\[(.*?)\]\]></wp:meta_key>\s*"
    r"<wp:meta_value><!\[CDATA\[(.*?)\]\]></wp:meta_value>\s*</wp:postmeta>",
    re.S,
)


@dataclass
class Entry:
    post_id: int
    post_type: str
    status: str
    title: str
    slug: str
    link: str
    content: str
    meta: dict[str, str]
    attachment_url: str = ""
    route_rel: str = ""
    downloaded_files: list[tuple[str, str]] = field(default_factory=list)


def field(block: str, name: str) -> str:
    cdata = re.search(CDATA_FIELD_RE.format(name=re.escape(name)), block, re.S)
    if cdata:
        return cdata.group(1)
    plain = re.search(TEXT_FIELD_RE.format(name=re.escape(name)), block, re.S)
    return plain.group(1) if plain else ""


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\x19", "'").replace("\x1a", "'").replace("\x1c", "")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value.strip()


def parse_entry(block: str) -> Entry:
    return Entry(
        post_id=int(clean_text(field(block, "wp:post_id")) or 0),
        post_type=clean_text(field(block, "wp:post_type")),
        status=clean_text(field(block, "wp:status")),
        title=clean_text(field(block, "title")),
        slug=clean_text(field(block, "wp:post_name")),
        link=clean_text(field(block, "link")),
        content=field(block, "content:encoded"),
        meta={clean_text(k): clean_text(v) for k, v in META_RE.findall(block)},
        attachment_url=clean_text(field(block, "wp:attachment_url")),
    )


def route_for_entry(entry: Entry) -> str:
    parsed = urlparse(entry.link)
    path = parsed.path.strip("/")
    if entry.post_type == "page" and entry.slug == "home":
        return "index.html"
    if path:
        return f"{path}/index.html"
    if entry.post_type == "page":
        return f"{entry.slug}/index.html"
    return f"{entry.post_type}/{entry.slug}/index.html"


def relative_url(from_rel: str, to_rel: str) -> str:
    return str(Path(to_rel).relative_to(".")) if from_rel == to_rel else str(
        Path(
            Path("../" * max(len(Path(from_rel).parents) - 1, 0))
        )
    )


def rel_link(from_rel: str, to_rel: str) -> str:
    return str(Path(to_rel).relative_to(Path(from_rel).parent)) if Path(from_rel).parent == Path(".") else str(
        Path(re.sub(r"[^/]+$", "", from_rel) or ".").joinpath(".")
    )


def relative_href(from_rel: str, to_rel: str) -> str:
    return str(Path(to_rel).relative_to(Path(from_rel).parent)) if False else re.sub(
        r"\\", "/", str(Path(Path(to_rel)).relative_to(Path(".")))  # placeholder
    )


def relpath_href(from_rel: str, to_rel: str) -> str:
    return re.sub(r"\\", "/", str(Path(to_rel).relative_to(Path(to_rel).anchor)) if False else "")


def make_rel(from_rel: str, to_rel: str) -> str:
    return re.sub(
        r"\\",
        "/",
        str(Path(to_rel) if from_rel == to_rel else Path(to_rel).relative_to(Path(".")))
        if Path(from_rel).parent == Path(".")
        else str(Path("../../")),
    )


def rel_url(from_rel: str, to_rel: str) -> str:
    from_path = Path(from_rel)
    to_path = Path(to_rel)
    return re.sub(r"\\", "/", str(Path(shutil.os.path.relpath(to_path, from_path.parent))))


def is_image_url(url: str) -> bool:
    return bool(re.search(r"\.(jpg|jpeg|png|gif|webp|svg)(\?.*)?$", url, re.I))


def wayback_url(url: str) -> str:
    qualifier = "im_" if is_image_url(url) else ""
    return f"https://web.archive.org/web/{WAYBACK_STAMP}{qualifier}/{url}"


def copy_theme_assets() -> None:
    preserved_uploads = Path("/tmp/lateraleconomics-xml-site-uploads")
    uploads_dir = OUTPUT_DIR / "wp-content/uploads"
    if preserved_uploads.exists():
        shutil.rmtree(preserved_uploads)
    if uploads_dir.exists():
        shutil.copytree(uploads_dir, preserved_uploads)
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WP_CONTENT_SOURCE, OUTPUT_DIR / "wp-content")
    if preserved_uploads.exists():
        shutil.copytree(preserved_uploads, OUTPUT_DIR / "wp-content/uploads", dirs_exist_ok=True)
        shutil.rmtree(preserved_uploads)


def embed_markup(url: str, width: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    src = url
    height = "203"
    if "youtu.be" in netloc:
        video_id = parsed.path.strip("/")
        src = f"https://www.youtube.com/embed/{video_id}"
    elif "youtube.com" in netloc and parsed.path == "/watch":
        query = dict(part.split("=", 1) for part in parsed.query.split("&") if "=" in part)
        video_id = query.get("v", "")
        src = f"https://www.youtube.com/embed/{video_id}" if video_id else url
    elif "vimeo.com" in netloc and parsed.path.strip("/").isdigit():
        src = f"https://player.vimeo.com/video/{parsed.path.strip('/')}"
        height = "203"
    elif "player.vimeo.com" in netloc:
        src = url
        height = "203"
    return (
        f'<p><iframe loading="lazy" width="{html.escape(width or "360")}" '
        f'height="{height}" src="{html.escape(src)}" frameborder="0" allowfullscreen></iframe></p>'
    )


def preprocess_fragment(fragment: str) -> str:
    text = clean_text(fragment)

    def replace_embed(match: re.Match[str]) -> str:
        width = match.group(1) or "360"
        url = clean_text(match.group(2))
        return embed_markup(url, width)

    text = re.sub(
        r"\[embed(?:\s+width=\"(\d+)\")?\](.*?)\[/embed\]",
        replace_embed,
        text,
        flags=re.S,
    )
    text = re.sub(
        r"\[yikes-mailchimp[^\]]*\]",
        '<p><em>Subscription form omitted in the local static copy.</em></p>',
        text,
        flags=re.I,
    )
    return text


def download_to_output(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.netloc not in SITE_HOSTS:
        return None
    target_rel = parsed.path.lstrip("/")
    if not target_rel:
        return None
    target = OUTPUT_DIR / target_rel
    if target.exists():
        return target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    source_local = Path("site-local") / target_rel
    if source_local.exists():
        shutil.copy2(source_local, target)
        return target_rel
    request = Request(wayback_url(url), headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=30) as response:
            target.write_bytes(response.read())
        return target_rel
    except (HTTPError, URLError, TimeoutError):
        return None


def extract_media_urls(entries: list[Entry], attachments: dict[int, Entry]) -> list[str]:
    urls: set[str] = set()
    for entry in entries:
        urls.update(re.findall(r'https?://[^\s<>"\']+', entry.content))
    for attachment in attachments.values():
        if attachment.attachment_url:
            urls.add(attachment.attachment_url)
    return sorted(urls)


def build_download_map(entries: list[Entry], attachments: dict[int, Entry]) -> dict[str, str]:
    media_map: dict[str, str] = {}
    for url in extract_media_urls(entries, attachments):
        parsed = urlparse(clean_text(url))
        if parsed.netloc not in SITE_HOSTS:
            continue
        rel = download_to_output(url)
        if rel:
            media_map[parsed.path] = rel
            media_map[url] = rel
    return media_map


def rewrite_fragment(fragment: str, current_rel: str, route_map: dict[str, str], media_map: dict[str, str]) -> str:
    soup = BeautifulSoup(preprocess_fragment(fragment), "html.parser")

    def rewrite_url(url: str) -> str:
        clean = clean_text(url)
        if not clean:
            return clean
        clean = re.sub(r"^https?://web\.archive\.org/web/\d+(?:[a-z_]+)?/", "", clean)
        parsed = urlparse(clean)
        if parsed.scheme in {"mailto", "tel"}:
            return clean
        if parsed.netloc in SITE_HOSTS:
            if parsed.path in media_map:
                return rel_url(current_rel, media_map[parsed.path])
            if clean in media_map:
                return rel_url(current_rel, media_map[clean])
            route = route_map.get(parsed.path) or route_map.get(parsed.path.rstrip("/") + "/")
            if route:
                return rel_url(current_rel, route)
        return clean

    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            img["src"] = rewrite_url(src)
        if img.get("srcset"):
            parts = []
            for chunk in img["srcset"].split(","):
                bits = chunk.strip().split()
                if not bits:
                    continue
                bits[0] = rewrite_url(bits[0])
                parts.append(" ".join(bits))
            img["srcset"] = ", ".join(parts)

    for tag in soup.find_all(href=True):
        tag["href"] = rewrite_url(tag["href"])

    for iframe in soup.find_all("iframe"):
        src = iframe.get("src")
        if src:
            iframe["src"] = rewrite_url(src)

    return str(soup)


def nav_markup(current_slug: str, route_map: dict[str, str], current_rel: str) -> str:
    labels = {
        "home": "Home",
        "who-we-are": "Who we are",
        "outputs": "Outputs",
        "presentations": "Presentations",
        "archives": "Archives",
        "clients": "Clients",
        "testimonials": "Testimonials",
        "contact-us": "Contact Us",
    }
    items = []
    for slug in NAV_SLUGS:
        route = route_map.get(f"/{slug}/", "") if slug != "home" else route_map.get("/", "index.html")
        href = rel_url(current_rel, route)
        current = ' aria-current="page"' if slug == current_slug else ""
        klass = " current-menu-item current_page_item" if slug == current_slug else ""
        items.append(
            f'<li class="menu-item{klass}"><a href="{href}"{current}>{labels[slug]}</a></li>'
        )
    return '<div class="menu-menu-1-container"><ul id="menu-menu-1" class="menu">' + "".join(items) + "</ul></div>"


def render_shell(title: str, body: str, current_slug: str, current_rel: str, route_map: dict[str, str]) -> str:
    style_href = rel_url(current_rel, "wp-content/themes/lateral/style.css")
    genericons_href = rel_url(current_rel, "wp-content/themes/twentyfourteen/genericons/genericons.css")
    blocks_href = rel_url(current_rel, "wp-content/themes/twentyfourteen/css/blocks.css")
    menu_gif = rel_url(current_rel, "wp-content/themes/lateral/images/menu2_r1_c1.gif")
    footer_home = rel_url(current_rel, route_map["/"])
    footer_who = rel_url(current_rel, route_map["/who-we-are/"])
    footer_outputs = rel_url(current_rel, route_map["/outputs/"])
    return f"""<!DOCTYPE html>
<html lang="en-AU">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<title>{html.escape(title)} - Lateral Economics - Capable, Innovative, Rigorous</title>
<link href="{style_href}" rel="stylesheet" type="text/css">
<link rel="stylesheet" id="genericons-css" href="{genericons_href}" media="all">
<link rel="stylesheet" id="twentyfourteen-block-style-css" href="{blocks_href}" media="all">
<style>
.xml-list {{ padding-left: 160px; padding-right: 30px; }}
.xml-list ul {{ margin: 0; padding-left: 20px; }}
.xml-list li {{ margin-bottom: 0.8rem; }}
.xml-docs {{ padding-left: 160px; padding-right: 30px; }}
.xml-docs ul {{ padding-left: 20px; }}
.xml-docs li {{ margin-bottom: 0.6rem; }}
.xml-note {{ color: #666; font-style: italic; }}
</style>
</head>
<body>
  <div class="header_letf">
    <img src="{menu_gif}" alt="">
  </div>
  <div class="header_banner"></div>
  <div class="nav_menu">
    {nav_markup(current_slug, route_map, current_rel)}
  </div>
  <div class="body_content">
    {body}
  </div>
  <div class="footer">
    <div class="footer_text">
      <a href="{footer_home}">Home </a>
      <a href="{footer_who}"> Who we are </a>
      <a href="{footer_outputs}"> Outputs </a>
      <a href="#"> Sitemap</a><br><br>
      <a href="http://www.keychange.com.au/">Website Development KeyChange</a><br><br>
      <a href="http://www.peachhomeloans.com.au/">Financial Services</a>
    </div>
  </div>
</body>
</html>
"""


def write_page(rel_path: str, content: str) -> None:
    target = OUTPUT_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def attachment_links(entry: Entry, attachments: dict[int, Entry], media_map: dict[str, str], current_rel: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    if entry.post_type == "output":
        specs = [
            ("pdf1", "text1"),
            ("pdf2", "text2"),
            ("pdf3", "text3"),
            ("doc1", "doc1"),
            ("ppt", "ppt"),
        ]
        for file_key, label_key in specs:
            attachment_id = entry.meta.get(file_key, "")
            if not attachment_id or not attachment_id.isdigit():
                continue
            attachment = attachments.get(int(attachment_id))
            if not attachment or not attachment.attachment_url:
                continue
            parsed = urlparse(attachment.attachment_url)
            local_rel = media_map.get(parsed.path)
            href = rel_url(current_rel, local_rel) if local_rel else attachment.attachment_url
            label = entry.meta.get(label_key) or attachment.title or Path(parsed.path).name
            links.append((label, href))
    elif entry.post_type == "archive":
        specs = [
            ("file1", "file_type_1"),
            ("file2", "file_type_2"),
            ("file3", "file_type_3"),
        ]
        for file_key, label_key in specs:
            attachment_id = entry.meta.get(file_key, "")
            if not attachment_id or not attachment_id.isdigit():
                continue
            attachment = attachments.get(int(attachment_id))
            if not attachment or not attachment.attachment_url:
                continue
            parsed = urlparse(attachment.attachment_url)
            local_rel = media_map.get(parsed.path)
            href = rel_url(current_rel, local_rel) if local_rel else attachment.attachment_url
            label = entry.meta.get(label_key) or attachment.title or Path(parsed.path).name
            links.append((label, href))
    return links


def localize_same_site_upload_links(root: Path) -> None:
    pattern = re.compile(r'https?://(?:www\.)?lateraleconomics\.com\.au(/wp-content/uploads/[^\s"\'<>)]*)')
    for path in root.rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")

        def repl(match: re.Match[str]) -> str:
            local_rel = match.group(1).lstrip("/")
            target = root / local_rel
            if not target.exists():
                return match.group(0)
            return rel_url(str(path.relative_to(root)), local_rel)

        updated = pattern.sub(repl, text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def listing_markup(items: list[Entry], current_rel: str) -> str:
    rows = []
    for entry in items:
        href = rel_url(current_rel, entry.route_rel)
        rows.append(f'<li><a href="{href}">{html.escape(entry.title)}</a></li>')
    return '<div class="xml-list"><ul>' + "".join(rows) + "</ul></div>"


def main() -> None:
    copy_theme_assets()

    blocks = ITEM_RE.findall(SOURCE_XML.read_text(encoding="utf-8", errors="ignore"))
    entries = [parse_entry(block) for block in blocks]
    attachments = {entry.post_id: entry for entry in entries if entry.post_type == "attachment"}
    pages = [entry for entry in entries if entry.status == "publish" and entry.post_type == "page"]
    outputs = [entry for entry in entries if entry.status == "publish" and entry.post_type == "output"]
    archives = [entry for entry in entries if entry.status == "publish" and entry.post_type == "archive"]

    site_entries = pages + outputs + archives
    route_map: dict[str, str] = {}
    for entry in site_entries:
        entry.route_rel = route_for_entry(entry)
        parsed = urlparse(entry.link)
        route_map[parsed.path or "/"] = entry.route_rel
        route_map[parsed.path.rstrip("/") + "/"] = entry.route_rel
    route_map["/"] = next(entry.route_rel for entry in pages if entry.slug == "home")

    media_map = build_download_map(site_entries, attachments)

    pages_by_slug = {entry.slug: entry for entry in pages}

    for entry in outputs + archives:
        body_bits = [f'<h1 style="padding-left: 160px;">{html.escape(entry.title)}</h1>']
        description = entry.meta.get("text4") or entry.meta.get("desc") or entry.meta.get("content") or ""
        if description:
            body_bits.append(f'<div class="xml-docs"><p>{html.escape(description)}</p></div>')
        if entry.content.strip():
            body_bits.append(rewrite_fragment(entry.content, entry.route_rel, route_map, media_map))
        links = attachment_links(entry, attachments, media_map, entry.route_rel)
        if links:
            items = "".join(f'<li><a href="{href}">{html.escape(label)}</a></li>' for label, href in links)
            body_bits.append(f'<div class="xml-docs"><ul>{items}</ul></div>')
        write_page(
            entry.route_rel,
            render_shell(entry.title, "".join(body_bits), entry.slug, entry.route_rel, route_map),
        )

    for entry in pages:
        body = rewrite_fragment(entry.content, entry.route_rel, route_map, media_map)
        if entry.slug == "outputs":
            body += listing_markup(outputs, entry.route_rel)
        elif entry.slug == "archives":
            body += listing_markup(archives, entry.route_rel)
        write_page(
            entry.route_rel,
            render_shell(entry.title, body, entry.slug, entry.route_rel, route_map),
        )

    localize_same_site_upload_links(OUTPUT_DIR)
    print(f"Built {OUTPUT_DIR} with {len(site_entries)} pages and {len(media_map)} local media files.")


if __name__ == "__main__":
    main()
