from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/elements/1.1/"
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("atom", ATOM_NS)


@dataclass(frozen=True)
class SourceEntry:
    source_name: str
    source_url: str
    entry_id: str
    title: str
    link: str
    author: str
    published: str
    content: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", delete=False, dir=path.parent
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value or "")
    value = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def text_of(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def first_text(node: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        found = node.find(name)
        value = text_of(found)
        if value:
            return value
    return ""


def stable_entry_id(source_url: str, raw_id: str, link: str, title: str) -> str:
    material = "\n".join([source_url, raw_id or link or title]).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def parse_rss(root: ET.Element, source_name: str, source_url: str) -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    for item in root.findall("./channel/item"):
        title = first_text(item, ["title"])
        link = first_text(item, ["link"])
        raw_id = first_text(item, ["guid"])
        author = first_text(item, [f"{{{DC_NS}}}creator", "author"])
        published = first_text(item, ["pubDate", f"{{{DC_NS}}}date"])
        content = first_text(
            item,
            [f"{{{CONTENT_NS}}}encoded", "description", "summary", "content"],
        )
        if not title and not link:
            continue
        entries.append(
            SourceEntry(
                source_name=source_name,
                source_url=source_url,
                entry_id=stable_entry_id(source_url, raw_id, link, title),
                title=title or link,
                link=link or source_url,
                author=author,
                published=published,
                content=strip_html(content),
            )
        )
    return entries


def parse_atom(root: ET.Element, source_name: str, source_url: str) -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    namespace = "" if root.tag == "feed" else f"{{{ATOM_NS}}}"
    for item in root.findall(f"{namespace}entry"):
        title = first_text(item, [f"{namespace}title"])
        raw_id = first_text(item, [f"{namespace}id"])
        published = first_text(item, [f"{namespace}published", f"{namespace}updated"])
        content = first_text(item, [f"{namespace}content", f"{namespace}summary"])
        author = first_text(item, [f"{namespace}author/{namespace}name"])
        link = ""
        for link_node in item.findall(f"{namespace}link"):
            rel = link_node.attrib.get("rel", "alternate")
            href = link_node.attrib.get("href", "")
            if href and rel in {"alternate", ""}:
                link = href
                break
        if not link:
            link_node = item.find(f"{namespace}link")
            if link_node is not None:
                link = link_node.attrib.get("href", "") or text_of(link_node)
        if not title and not link:
            continue
        entries.append(
            SourceEntry(
                source_name=source_name,
                source_url=source_url,
                entry_id=stable_entry_id(source_url, raw_id, link, title),
                title=title or link,
                link=link or source_url,
                author=author,
                published=published,
                content=strip_html(content),
            )
        )
    return entries


def parse_feed(xml_text: str, source_name: str, source_url: str) -> list[SourceEntry]:
    root = ET.fromstring(xml_text)
    local_name = root.tag.rsplit("}", 1)[-1].lower()
    if local_name in {"rss", "rdf"}:
        return parse_rss(root, source_name, source_url)
    if local_name == "feed":
        return parse_atom(root, source_name, source_url)
    raise ValueError(f"不支援的訂閱源格式：{root.tag}")


def fetch_text(url: str, timeout: int, config_dir: Path) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        path = Path(urllib.request.url2pathname(parsed.path))
        return path.read_text(encoding="utf-8")
    if not parsed.scheme:
        path = (config_dir / url).resolve()
        return path.read_text(encoding="utf-8")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GPT-Feed-Bridge/1.0 (+personal RSS summarizer)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def article_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "translated_title": {"type": "string"},
            "original_language": {"type": "string"},
            "summary_zh_tw": {"type": "string"},
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 5,
            },
            "why_relevant": {"type": "string"},
            "terms": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "term": {"type": "string"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["term", "explanation"],
                },
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 6,
            },
            "full_translation": {"type": ["string", "null"]},
        },
        "required": [
            "translated_title",
            "original_language",
            "summary_zh_tw",
            "key_points",
            "why_relevant",
            "terms",
            "tags",
            "full_translation",
        ],
    }


def extract_response_text(payload: dict[str, Any]) -> str:
    for output in payload.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"])
            if content.get("type") == "refusal":
                raise RuntimeError(f"OpenAI 拒絕處理：{content.get('refusal', '未知原因')}")
    raise RuntimeError("OpenAI 回應中沒有可用文字。")


def analyze_with_openai(
    entry: SourceEntry,
    config: dict[str, Any],
    api_key: str,
    timeout: int,
) -> dict[str, Any]:
    ai = config["openai"]
    max_chars = int(ai.get("max_input_characters", 16000))
    include_full = bool(ai.get("include_full_translation", False))
    interests = "、".join(config.get("interests", [])) or "一般知識閱讀"
    developer_prompt = (
        "你是個人閱讀助理。請只根據提供的文章內容產生資料，不得補寫原文沒有的事實。"
        f"輸出語言為{ai.get('target_language', '繁體中文（台灣）')}。"
        "保留人名、作品名、日期、數字與必要的原文專有名詞。"
        "摘要應適合在 RSS 閱讀器快速判斷是否值得閱讀全文。"
        f"讀者興趣：{interests}。"
        + (
            "請在 full_translation 提供忠實的完整翻譯，保留段落；若來源內容明顯不完整，翻譯現有內容並註明。"
            if include_full
            else "full_translation 必須為 null；不要重製全文。"
        )
    )
    article_payload = {
        "source": entry.source_name,
        "title": entry.title,
        "author": entry.author,
        "published": entry.published,
        "url": entry.link,
        "content": entry.content[:max_chars],
    }
    request_payload = {
        "model": ai.get("model", "gpt-5.6-luna"),
        "store": False,
        "reasoning": {"effort": ai.get("reasoning_effort", "none")},
        "input": [
            {"role": "developer", "content": developer_prompt},
            {
                "role": "user",
                "content": "請整理這篇文章：\n" + json.dumps(article_payload, ensure_ascii=False),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "article_digest",
                "strict": True,
                "schema": article_schema(),
            }
        },
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API 錯誤 {error.code}：{body[:1000]}") from error
    return json.loads(extract_response_text(payload))


def analyze_mock(entry: SourceEntry, config: dict[str, Any]) -> dict[str, Any]:
    content = entry.content or entry.title
    excerpt = content[:180].strip()
    interests = config.get("interests", [])
    return {
        "translated_title": entry.title,
        "original_language": "測試資料",
        "summary_zh_tw": f"模擬摘要：{excerpt}",
        "key_points": [
            "確認來源訂閱源可被解析。",
            "確認文章識別碼可以避免重複處理。",
            "確認產出的繁體中文內容能寫入 RSS。",
        ],
        "why_relevant": f"用來驗證閱讀流程；設定的興趣包含：{'、'.join(interests[:3])}。",
        "terms": [{"term": "RSS", "explanation": "網站內容的標準訂閱格式。"}],
        "tags": ["測試", "GPT摘要"],
        "full_translation": None,
    }


def parse_datetime(value: str) -> datetime:
    if value:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            if parsed:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return utc_now()


def item_html(item: dict[str, Any]) -> str:
    analysis = item["analysis"]
    esc = html.escape
    parts = [
        f"<p><strong>原文：</strong><a href=\"{esc(item['link'], quote=True)}\">{esc(item['original_title'])}</a></p>",
        f"<p><strong>來源：</strong>{esc(item['source_name'])}"
        + (f" ｜ <strong>作者：</strong>{esc(item['author'])}" if item.get("author") else "")
        + "</p>",
        f"<h2>繁中摘要</h2><p>{esc(analysis['summary_zh_tw'])}</p>",
        "<h2>重點</h2><ul>"
        + "".join(f"<li>{esc(point)}</li>" for point in analysis["key_points"])
        + "</ul>",
        f"<h2>與我的興趣關聯</h2><p>{esc(analysis['why_relevant'])}</p>",
    ]
    if analysis.get("terms"):
        parts.append(
            "<h2>專有名詞</h2><dl>"
            + "".join(
                f"<dt><strong>{esc(term['term'])}</strong></dt><dd>{esc(term['explanation'])}</dd>"
                for term in analysis["terms"]
            )
            + "</dl>"
        )
    if analysis.get("full_translation"):
        paragraphs = "".join(
            f"<p>{esc(paragraph)}</p>"
            for paragraph in str(analysis["full_translation"]).split("\n")
            if paragraph.strip()
        )
        parts.append("<h2>全文翻譯</h2>" + paragraphs)
    parts.append(
        f"<hr><p><small>由 {esc(item['model'])} 產生於 {esc(item['generated_at'])}；請以原文為準。</small></p>"
    )
    return "".join(parts)


def build_rss(config: dict[str, Any], items: list[dict[str, Any]]) -> str:
    feed = config["feed"]
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = feed["title"]
    ET.SubElement(channel, "link").text = feed.get("home_url", feed.get("public_url", ""))
    ET.SubElement(channel, "description").text = feed["description"]
    ET.SubElement(channel, "language").text = feed.get("language", "zh-TW")
    ET.SubElement(channel, "lastBuildDate").text = email.utils.format_datetime(utc_now())
    public_url = feed.get("public_url")
    if public_url:
        ET.SubElement(
            channel,
            f"{{{ATOM_NS}}}link",
            {"href": public_url, "rel": "self", "type": "application/rss+xml"},
        )
    for stored in sorted(items, key=lambda value: value["sort_timestamp"], reverse=True):
        node = ET.SubElement(channel, "item")
        title = stored["analysis"]["translated_title"] or stored["original_title"]
        ET.SubElement(node, "title").text = f"【GPT 精讀】{title}"
        ET.SubElement(node, "link").text = stored["link"]
        ET.SubElement(node, "guid", {"isPermaLink": "false"}).text = f"gpt-feed:{stored['entry_id']}"
        ET.SubElement(node, "pubDate").text = email.utils.format_datetime(
            datetime.fromtimestamp(stored["sort_timestamp"], timezone.utc)
        )
        ET.SubElement(node, "description").text = stored["analysis"]["summary_zh_tw"]
        ET.SubElement(node, f"{{{CONTENT_NS}}}encoded").text = item_html(stored)
        if stored.get("author"):
            ET.SubElement(node, "author").text = stored["author"]
        for tag in stored["analysis"].get("tags", []):
            ET.SubElement(node, "category").text = tag
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def resolve_path(config_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else config_dir / path


def run(config_path: Path, mock_ai: bool = False) -> dict[str, Any]:
    config_path = config_path.resolve()
    config_dir = config_path.parent
    config = load_json(config_path)
    paths = config["paths"]
    state_path = resolve_path(config_dir, paths["state"])
    output_path = resolve_path(config_dir, paths["output"])
    status_path = resolve_path(config_dir, paths["status"])
    state = load_json(state_path, {"version": 1, "items": []})
    stored_items: list[dict[str, Any]] = list(state.get("items", []))
    processed_ids = {item["entry_id"] for item in stored_items}
    processing = config["processing"]
    timeout = int(processing.get("request_timeout_seconds", 30))
    max_source_items = int(processing.get("max_source_items", 20))
    initial_backfill = int(processing.get("initial_backfill_per_source", 1))
    max_new = int(processing.get("max_new_items_per_run", 5))
    candidates: list[SourceEntry] = []
    errors: list[str] = []

    enabled_sources = [source for source in config.get("sources", []) if source.get("enabled", True)]
    for source in enabled_sources:
        name = source.get("name") or source["url"]
        try:
            xml_text = fetch_text(source["url"], timeout, config_dir)
            entries = parse_feed(xml_text, name, source["url"])
            source_seen = any(item.get("source_url") == source["url"] for item in stored_items)
            source_limit = max_source_items if source_seen else max(0, initial_backfill)
            candidates.extend(entry for entry in entries[:source_limit] if entry.entry_id not in processed_ids)
        except Exception as error:  # keep other sources running
            errors.append(f"{name}: {error}")

    candidates.sort(key=lambda entry: parse_datetime(entry.published), reverse=True)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if candidates and not mock_ai and not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY；請設定環境變數或 GitHub Actions Secret。")

    created = 0
    for entry in candidates[:max_new]:
        try:
            analysis = analyze_mock(entry, config) if mock_ai else analyze_with_openai(
                entry, config, api_key, timeout
            )
            published_dt = parse_datetime(entry.published)
            stored_items.append(
                {
                    "entry_id": entry.entry_id,
                    "source_name": entry.source_name,
                    "source_url": entry.source_url,
                    "original_title": entry.title,
                    "link": entry.link,
                    "author": entry.author,
                    "published": entry.published,
                    "sort_timestamp": published_dt.timestamp(),
                    "generated_at": iso_now(),
                    "model": "mock" if mock_ai else config["openai"]["model"],
                    "analysis": analysis,
                }
            )
            created += 1
        except Exception as error:
            errors.append(f"{entry.title}: {error}")

    max_output = int(processing.get("max_output_items", 100))
    stored_items = sorted(
        stored_items, key=lambda value: value["sort_timestamp"], reverse=True
    )[:max_output]
    state_payload = {"version": 1, "updated_at": iso_now(), "items": stored_items}
    write_json_atomic(state_path, state_payload)
    write_text_atomic(output_path, build_rss(config, stored_items))
    status_payload = {
        "status": "ok" if not errors else "completed_with_errors",
        "updated_at": iso_now(),
        "enabled_sources": len(enabled_sources),
        "new_items": created,
        "total_items": len(stored_items),
        "errors": errors,
    }
    write_json_atomic(status_path, status_payload)
    return status_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="產生由 GPT 更新的繁體中文 RSS 訂閱源。")
    parser.add_argument("--config", default="config.json", help="設定檔路徑")
    parser.add_argument("--mock-ai", action="store_true", help="不用 API，產生可驗證格式的模擬摘要")
    args = parser.parse_args(argv)
    try:
        result = run(Path(args.config), mock_ai=args.mock_ai)
    except Exception as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
