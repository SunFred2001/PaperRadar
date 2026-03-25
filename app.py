from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
CACHE_PATH = DATA_DIR / "papers_cache.json"

MONTH_MAP = {
    "jan": "01",
    "january": "01",
    "feb": "02",
    "february": "02",
    "mar": "03",
    "march": "03",
    "apr": "04",
    "april": "04",
    "may": "05",
    "jun": "06",
    "june": "06",
    "jul": "07",
    "july": "07",
    "aug": "08",
    "august": "08",
    "sep": "09",
    "sept": "09",
    "september": "09",
    "oct": "10",
    "october": "10",
    "nov": "11",
    "november": "11",
    "dec": "12",
    "december": "12",
}

DEFAULT_CONFIG = {
    "search_query": (
        '((deep learning[Title/Abstract] OR "neural network*"[Title/Abstract] '
        'OR transformer[Title/Abstract]) AND (ECG[Title/Abstract] '
        'OR electrocardiogram[Title/Abstract] '
        'OR electrocardiography[Title/Abstract]) AND ("signal processing"[Title/Abstract] '
        'OR classification[Title/Abstract] OR diagnosis[Title/Abstract] '
        'OR denoising[Title/Abstract])) AND ("journal article"[Publication Type])'
    ),
    "max_results": 15,
    "lookback_days": 90,
    "refresh_interval_hours": 24,
    "tool_name": "ecg-paper-radar",
    "contact_email": "",
    "push_enabled": False,
    "push_channel": "serverchan",
    "push_time": "09:00",
    "push_timezone": "Asia/Shanghai",
    "push_max_papers": 5,
    "serverchan_sendkey": "",
    "wecom_webhook_url": "",
}

DEFAULT_CACHE = {
    "source": "PubMed",
    "status": "idle",
    "message": "Waiting for first refresh.",
    "last_attempt_at": None,
    "last_success_at": None,
    "count": 0,
    "papers": [],
    "error": None,
    "last_push_at": None,
    "last_push_attempt_at": None,
    "last_push_attempt_day": None,
    "last_push_status": "idle",
    "last_push_message": "WeChat push is disabled.",
    "last_push_error": None,
    "last_push_count": 0,
    "notified_paper_ids": [],
}

NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SERVERCHAN_API_TEMPLATE = "https://sctapi.ftqq.com/{sendkey}.send"
PUSH_TRACKING_FIELDS = ("search_query", "max_results", "lookback_days")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def compact_text(value: str) -> str:
    return " ".join(value.split())


def flatten_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return compact_text("".join(node.itertext()))


def json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def json_load(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback.copy()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback.copy()


def normalize_month(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.strip().lower()
    if token.isdigit():
        return token.zfill(2)
    return MONTH_MAP.get(token)


def compose_date(year: str | None, month: str | None, day: str | None) -> str:
    if not year:
        return ""
    if month and day:
        return f"{year}-{month}-{day.zfill(2)}"
    if month:
        return f"{year}-{month}"
    return year


def extract_date(node: ET.Element | None) -> str:
    if node is None:
        return ""
    year = flatten_text(node.find("Year")) or None
    month = normalize_month(flatten_text(node.find("Month")) or None)
    day = flatten_text(node.find("Day")) or None
    if year:
        return compose_date(year, month, day)
    return flatten_text(node.find("MedlineDate"))


def parse_pub_date(article: ET.Element) -> str:
    date_candidates = [
        article.find(".//PubmedData/History/PubMedPubDate[@PubStatus='pubmed']"),
        article.find(".//PubmedData/History/PubMedPubDate[@PubStatus='entrez']"),
        article.find(".//ArticleDate"),
        article.find(".//JournalIssue/PubDate"),
    ]
    for candidate in date_candidates:
        parsed = extract_date(candidate)
        if parsed:
            return parsed
    return ""


def parse_authors(article: ET.Element) -> list[str]:
    authors: list[str] = []
    for node in article.findall(".//AuthorList/Author"):
        collective = flatten_text(node.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        last_name = flatten_text(node.find("LastName"))
        fore_name = flatten_text(node.find("ForeName"))
        initials = flatten_text(node.find("Initials"))
        if last_name and fore_name:
            authors.append(f"{fore_name} {last_name}")
        elif last_name and initials:
            authors.append(f"{initials} {last_name}")
        elif last_name:
            authors.append(last_name)
    return authors


def parse_abstract(article: ET.Element) -> str:
    sections: list[str] = []
    for part in article.findall(".//Abstract/AbstractText"):
        label = part.attrib.get("Label", "").strip()
        text = flatten_text(part)
        if not text:
            continue
        sections.append(f"{label}: {text}" if label else text)
    return "\n\n".join(sections)


def extract_doi(article: ET.Element) -> str:
    for node in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if node.attrib.get("IdType") == "doi":
            doi = flatten_text(node)
            if doi:
                return doi
    return ""


def parse_publication_types(article: ET.Element) -> list[str]:
    values: list[str] = []
    for node in article.findall(".//PublicationTypeList/PublicationType"):
        text = flatten_text(node)
        if text:
            values.append(text)
    return values


def parse_article(article: ET.Element) -> dict[str, Any]:
    citation = article.find("MedlineCitation")
    if citation is None:
        return {}

    pmid = flatten_text(citation.find("PMID"))
    title = flatten_text(citation.find(".//Article/ArticleTitle"))
    journal = flatten_text(citation.find(".//Article/Journal/Title"))
    abstract = parse_abstract(citation)
    authors = parse_authors(citation)
    doi = extract_doi(article)
    publication_types = parse_publication_types(citation)
    published_on = parse_pub_date(article)

    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "abstract": abstract,
        "authors": authors,
        "published_on": published_on,
        "doi": doi,
        "publication_types": publication_types,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        "doi_url": f"https://doi.org/{doi}" if doi else "",
    }


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def parse_iso_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_zone(tz_name: str | None) -> timezone | ZoneInfo:
    candidate = (tz_name or DEFAULT_CONFIG["push_timezone"]).strip() or DEFAULT_CONFIG["push_timezone"]
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))


def parse_push_time(value: str | None) -> tuple[int, int]:
    raw = (value or DEFAULT_CONFIG["push_time"]).strip()
    try:
        hours_raw, minutes_raw = raw.split(":", 1)
        hours = int(hours_raw)
        minutes = int(minutes_raw)
    except (AttributeError, ValueError):
        return 9, 0
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return 9, 0
    return hours, minutes


def paper_identity(paper: dict[str, Any]) -> str:
    for key in ("pmid", "doi", "title"):
        value = str(paper.get(key, "")).strip()
        if value:
            return value
    return ""


def truncate_text(text: str, limit: int) -> str:
    cleaned = compact_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 1)].rstrip()}..."


def render_authors(authors: list[str], limit: int = 4) -> str:
    if not authors:
        return "Unknown authors"
    visible = authors[:limit]
    suffix = "" if len(authors) <= limit else f" and {len(authors) - limit} more"
    return ", ".join(visible) + suffix


def ncbi_request(url: str, params: dict[str, Any], accept: str) -> bytes:
    query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
    request = Request(
        f"{url}?{query}",
        headers={
            "User-Agent": "ECG-Paper-Radar/1.0 (+https://pubmed.ncbi.nlm.nih.gov/)",
            "Accept": accept,
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read()


def fetch_latest_papers(config: dict[str, Any]) -> list[dict[str, Any]]:
    local_today = utc_now().astimezone(load_zone(str(config.get("push_timezone", "")))).date()
    lookback_days = max(1, int(config["lookback_days"]))
    start_day = local_today - timedelta(days=lookback_days)

    esearch_params = {
        "db": "pubmed",
        "retmode": "json",
        "retmax": max(1, min(50, int(config["max_results"]))),
        "sort": "pub date",
        "datetype": "pdat",
        "mindate": start_day.isoformat(),
        "maxdate": local_today.isoformat(),
        "term": config["search_query"],
        "tool": config.get("tool_name", DEFAULT_CONFIG["tool_name"]),
        "email": config.get("contact_email", ""),
    }

    raw_search = ncbi_request(NCBI_ESEARCH_URL, esearch_params, "application/json")
    search_payload = json.loads(raw_search.decode("utf-8"))
    paper_ids = search_payload.get("esearchresult", {}).get("idlist", [])
    if not paper_ids:
        return []

    efetch_params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": ",".join(paper_ids),
        "tool": config.get("tool_name", DEFAULT_CONFIG["tool_name"]),
        "email": config.get("contact_email", ""),
    }
    raw_fetch = ncbi_request(NCBI_EFETCH_URL, efetch_params, "application/xml")
    xml_root = ET.fromstring(raw_fetch)

    papers: list[dict[str, Any]] = []
    for article in xml_root.findall("PubmedArticle"):
        parsed = parse_article(article)
        if parsed.get("title"):
            papers.append(parsed)
    return papers


def send_serverchan_push(sendkey: str, title: str, body: str) -> str:
    endpoint = SERVERCHAN_API_TEMPLATE.format(sendkey=sendkey)
    payload = urlencode({"title": title, "desp": body}).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    if parsed.get("code") != 0:
        raise ValueError(parsed.get("message") or "ServerChan push failed.")
    return str(parsed.get("message") or "ServerChan push sent.")


def send_wecom_bot_push(webhook_url: str, title: str, body: str) -> str:
    content = f"## {title}\n{body}"
    payload = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}).encode("utf-8")
    request = Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    if parsed.get("errcode") != 0:
        raise ValueError(parsed.get("errmsg") or "WeCom bot push failed.")
    return "WeCom bot push sent."


class PaperMonitor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.config = self._load_config()
        self.cache = self._load_cache()
        self.scheduler = threading.Thread(target=self._scheduler_loop, daemon=True)

    def ensure_storage(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            json_dump(CONFIG_PATH, DEFAULT_CONFIG)
        if not CACHE_PATH.exists():
            json_dump(CACHE_PATH, DEFAULT_CACHE)

    def _load_config(self) -> dict[str, Any]:
        self.ensure_storage()
        stored = json_load(CONFIG_PATH, DEFAULT_CONFIG)
        merged = DEFAULT_CONFIG.copy()
        merged.update(stored)
        return merged

    def _load_cache(self) -> dict[str, Any]:
        self.ensure_storage()
        stored = json_load(CACHE_PATH, DEFAULT_CACHE)
        merged = DEFAULT_CACHE.copy()
        merged.update(stored)
        if not isinstance(merged.get("notified_paper_ids"), list):
            merged["notified_paper_ids"] = []
        return merged

    def _save_config(self) -> None:
        json_dump(CONFIG_PATH, self.config)

    def _save_cache(self) -> None:
        json_dump(CACHE_PATH, self.cache)

    def _push_zone(self) -> timezone | ZoneInfo:
        return load_zone(str(self.config.get("push_timezone", "")))

    def _push_now(self) -> datetime:
        return utc_now().astimezone(self._push_zone())

    def _today_push_key(self) -> str:
        return self._push_now().date().isoformat()

    def _scheduled_push_datetime(self, on_date: date | None = None) -> datetime:
        local_now = self._push_now()
        target_date = on_date or local_now.date()
        hours, minutes = parse_push_time(str(self.config.get("push_time", "")))
        return datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            hours,
            minutes,
            tzinfo=self._push_zone(),
        )

    def _refresh_due(self) -> bool:
        last_success = parse_iso_datetime(self.cache.get("last_success_at"))
        if not last_success:
            return True
        due_at = last_success + timedelta(hours=int(self.config["refresh_interval_hours"]))
        return utc_now() >= due_at

    def _push_due(self) -> bool:
        if not coerce_bool(self.config.get("push_enabled")):
            return False
        today_key = self._today_push_key()
        if self.cache.get("last_push_attempt_day") == today_key:
            return False
        return self._push_now() >= self._scheduled_push_datetime()

    def _next_daily_push_at(self) -> str | None:
        if not coerce_bool(self.config.get("push_enabled")):
            return None
        local_now = self._push_now()
        today_key = local_now.date().isoformat()
        today_slot = self._scheduled_push_datetime(local_now.date())
        if self.cache.get("last_push_attempt_day") != today_key and local_now <= today_slot:
            return today_slot.isoformat()
        tomorrow_slot = self._scheduled_push_datetime(local_now.date() + timedelta(days=1))
        return tomorrow_slot.isoformat()

    def next_refresh_at(self) -> str | None:
        last_success = parse_iso_datetime(self.cache.get("last_success_at"))
        if not last_success:
            return iso_now()
        return (last_success + timedelta(hours=int(self.config["refresh_interval_hours"]))).isoformat()

    def _validate_push_credentials(self) -> None:
        channel = str(self.config.get("push_channel", "serverchan")).strip()
        if channel == "serverchan":
            if not str(self.config.get("serverchan_sendkey", "")).strip():
                raise ValueError("ServerChan SendKey is required.")
            return
        if channel == "wecom_bot":
            if not str(self.config.get("wecom_webhook_url", "")).strip():
                raise ValueError("WeCom bot webhook URL is required.")
            return
        raise ValueError("Unsupported WeChat push channel.")

    def _update_push_state(
        self,
        *,
        status: str,
        message: str,
        error: str | None = None,
        count: int | None = None,
        mark_daily_attempt: bool = False,
        mark_sent: bool = False,
    ) -> None:
        self.cache["last_push_status"] = status
        self.cache["last_push_message"] = message
        self.cache["last_push_error"] = error
        if count is not None:
            self.cache["last_push_count"] = count
        if mark_sent:
            self.cache["last_push_at"] = iso_now()
        if mark_daily_attempt:
            self.cache["last_push_attempt_at"] = iso_now()
            self.cache["last_push_attempt_day"] = self._today_push_key()
        self._save_cache()

    def _select_new_papers_for_push(self) -> list[dict[str, Any]]:
        known_ids = {
            str(item).strip()
            for item in self.cache.get("notified_paper_ids", [])
            if str(item).strip()
        }
        selected: list[dict[str, Any]] = []
        for paper in self.cache.get("papers", []):
            identity = paper_identity(paper)
            if identity and identity in known_ids:
                continue
            selected.append(paper)
        limit = safe_int(self.config.get("push_max_papers"), DEFAULT_CONFIG["push_max_papers"], 1, 10)
        return selected[:limit]

    def _remember_pushed_papers(self, papers: list[dict[str, Any]]) -> None:
        existing = [
            str(item).strip()
            for item in self.cache.get("notified_paper_ids", [])
            if str(item).strip()
        ]
        seen = set(existing)
        for paper in papers:
            identity = paper_identity(paper)
            if identity and identity not in seen:
                existing.append(identity)
                seen.add(identity)
        self.cache["notified_paper_ids"] = existing[-500:]

    def _build_push_message(self, papers: list[dict[str, Any]], *, test_mode: bool) -> tuple[str, str]:
        local_now = self._push_now()
        title_prefix = "ECG paper push test" if test_mode else "ECG daily paper digest"
        title = f"{title_prefix} {local_now.strftime('%Y-%m-%d')}"
        lines = [
            f"Sent at: {local_now.strftime('%Y-%m-%d %H:%M')} ({self.config['push_timezone']})",
            f"Papers in this message: {len(papers)}",
            "",
        ]
        for index, paper in enumerate(papers, start=1):
            lines.append(f"{index}. {paper.get('title') or 'Untitled paper'}")
            lines.append(f"   Journal: {paper.get('journal') or 'Unknown journal'}")
            lines.append(f"   Published: {paper.get('published_on') or 'Unknown date'}")
            lines.append(f"   Authors: {render_authors(paper.get('authors') or [])}")
            abstract = truncate_text(str(paper.get("abstract") or "No abstract available."), 140)
            lines.append(f"   Abstract: {abstract}")
            if paper.get("pubmed_url"):
                lines.append(f"   PubMed: {paper['pubmed_url']}")
            if paper.get("doi_url"):
                lines.append(f"   DOI: {paper['doi_url']}")
            lines.append("")
        return title, "\n".join(lines).strip()

    def _dispatch_wechat_push(self, papers: list[dict[str, Any]], *, test_mode: bool) -> str:
        self._validate_push_credentials()
        title, body = self._build_push_message(papers, test_mode=test_mode)
        channel = str(self.config.get("push_channel", "serverchan")).strip()
        if channel == "serverchan":
            return send_serverchan_push(str(self.config["serverchan_sendkey"]).strip(), title, body)
        if channel == "wecom_bot":
            return send_wecom_bot_push(str(self.config["wecom_webhook_url"]).strip(), title, body)
        raise ValueError("Unsupported WeChat push channel.")

    def refresh(self) -> dict[str, Any]:
        with self.lock:
            self.config = self._load_config()
            self.cache["last_attempt_at"] = iso_now()
            self.cache["status"] = "loading"
            self.cache["message"] = "Refreshing PubMed results."
            self._save_cache()

            try:
                papers = fetch_latest_papers(self.config)
            except (HTTPError, URLError, TimeoutError, ET.ParseError, json.JSONDecodeError, ValueError) as exc:
                self.cache["status"] = "error"
                self.cache["error"] = str(exc)
                self.cache["message"] = "Refresh failed. Keeping previous cache."
                self._save_cache()
                return self.snapshot()

            self.cache.update(
                {
                    "source": "PubMed",
                    "status": "ready" if papers else "empty",
                    "message": "Refresh complete." if papers else "No papers matched the current query.",
                    "last_success_at": iso_now(),
                    "count": len(papers),
                    "papers": papers,
                    "error": None,
                }
            )
            self._save_cache()
            return self.snapshot()

    def send_test_push(self) -> dict[str, Any]:
        with self.lock:
            self.config = self._load_config()
            self.cache = self._load_cache()
            self._update_push_state(
                status="loading",
                message="Sending test WeChat push.",
                error=None,
                count=0,
                mark_daily_attempt=False,
                mark_sent=False,
            )

            if not self.cache.get("papers"):
                self.refresh()

            if self.cache.get("status") == "error":
                self._update_push_state(
                    status="error",
                    message="Test push cancelled because the paper cache could not be refreshed.",
                    error=str(self.cache.get("error") or "Refresh failed."),
                    count=0,
                    mark_daily_attempt=False,
                    mark_sent=False,
                )
                return self.snapshot()

            papers = list(self.cache.get("papers", []))
            if not papers:
                self._update_push_state(
                    status="empty",
                    message="No cached papers available for test push.",
                    error=None,
                    count=0,
                    mark_daily_attempt=False,
                    mark_sent=False,
                )
                return self.snapshot()

            limit = safe_int(self.config.get("push_max_papers"), DEFAULT_CONFIG["push_max_papers"], 1, 10)
            selected = papers[:limit]

            try:
                provider_message = self._dispatch_wechat_push(selected, test_mode=True)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                self._update_push_state(
                    status="error",
                    message="Test push failed.",
                    error=str(exc),
                    count=0,
                    mark_daily_attempt=False,
                    mark_sent=False,
                )
                return self.snapshot()

            self._update_push_state(
                status="ready",
                message=f"Test push sent successfully. {provider_message}",
                error=None,
                count=len(selected),
                mark_daily_attempt=False,
                mark_sent=True,
            )
            return self.snapshot()

    def _run_daily_push(self) -> None:
        with self.lock:
            self.config = self._load_config()
            self.cache = self._load_cache()
            self._update_push_state(
                status="loading",
                message="Checking for new papers and sending WeChat push.",
                error=None,
                count=0,
                mark_daily_attempt=True,
                mark_sent=False,
            )

            self.refresh()
            if self.cache.get("status") == "error":
                self._update_push_state(
                    status="error",
                    message="Daily push stopped because refresh failed.",
                    error=str(self.cache.get("error") or "Refresh failed."),
                    count=0,
                    mark_daily_attempt=True,
                    mark_sent=False,
                )
                return

            selected = self._select_new_papers_for_push()
            if not selected:
                self._update_push_state(
                    status="empty",
                    message="No new papers to send today.",
                    error=None,
                    count=0,
                    mark_daily_attempt=True,
                    mark_sent=True,
                )
                return

            try:
                provider_message = self._dispatch_wechat_push(selected, test_mode=False)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                self._update_push_state(
                    status="error",
                    message="Daily push failed.",
                    error=str(exc),
                    count=0,
                    mark_daily_attempt=True,
                    mark_sent=False,
                )
                return

            self._remember_pushed_papers(selected)
            self._update_push_state(
                status="ready",
                message=f"Daily push sent successfully. {provider_message}",
                error=None,
                count=len(selected),
                mark_daily_attempt=True,
                mark_sent=True,
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "cache": self.cache,
            "next_refresh_at": self.next_refresh_at(),
            "next_push_at": self._next_daily_push_at(),
        }

    def update_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.config = self._load_config()
            self.cache = self._load_cache()

            query = compact_text(str(payload.get("search_query", "")))
            if not query:
                raise ValueError("search_query cannot be empty")

            next_config = self.config.copy()
            next_config.update(
                {
                    "search_query": query,
                    "max_results": safe_int(payload.get("max_results"), DEFAULT_CONFIG["max_results"], 1, 50),
                    "lookback_days": safe_int(
                        payload.get("lookback_days"),
                        DEFAULT_CONFIG["lookback_days"],
                        1,
                        365,
                    ),
                    "refresh_interval_hours": safe_int(
                        payload.get("refresh_interval_hours"),
                        DEFAULT_CONFIG["refresh_interval_hours"],
                        1,
                        168,
                    ),
                    "contact_email": str(payload.get("contact_email", self.config.get("contact_email", ""))).strip(),
                    "push_enabled": coerce_bool(payload.get("push_enabled", self.config.get("push_enabled", False))),
                    "push_channel": str(payload.get("push_channel", self.config.get("push_channel", "serverchan"))).strip()
                    or "serverchan",
                    "push_time": str(payload.get("push_time", self.config.get("push_time", "09:00"))).strip() or "09:00",
                    "push_timezone": str(
                        payload.get("push_timezone", self.config.get("push_timezone", DEFAULT_CONFIG["push_timezone"]))
                    ).strip()
                    or DEFAULT_CONFIG["push_timezone"],
                    "push_max_papers": safe_int(
                        payload.get("push_max_papers"),
                        DEFAULT_CONFIG["push_max_papers"],
                        1,
                        10,
                    ),
                    "serverchan_sendkey": str(
                        payload.get("serverchan_sendkey", self.config.get("serverchan_sendkey", ""))
                    ).strip(),
                    "wecom_webhook_url": str(
                        payload.get("wecom_webhook_url", self.config.get("wecom_webhook_url", ""))
                    ).strip(),
                }
            )

            tracking_changed = any(
                str(self.config.get(field, "")) != str(next_config.get(field, "")) for field in PUSH_TRACKING_FIELDS
            )
            self.config = next_config
            self._save_config()

            if tracking_changed:
                self.cache["notified_paper_ids"] = []
                self.cache["last_push_at"] = None
                self.cache["last_push_attempt_at"] = None
                self.cache["last_push_attempt_day"] = None
                self.cache["last_push_count"] = 0
                self.cache["last_push_error"] = None
                self.cache["last_push_status"] = "idle"
                self.cache["last_push_message"] = "Push history was reset because the search scope changed."
                self._save_cache()

            return self.refresh()

    def start(self) -> None:
        self.scheduler.start()
        if self._refresh_due():
            threading.Thread(target=self.refresh, daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()

    def _scheduler_loop(self) -> None:
        while not self.stop_event.wait(60):
            self.config = self._load_config()
            self.cache = self._load_cache()
            if self._refresh_due():
                self.refresh()
            if self._push_due():
                self._run_daily_push()


class PaperRequestHandler(BaseHTTPRequestHandler):
    monitor: PaperMonitor

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/dashboard":
            self._send_json(self.monitor.snapshot())
            return
        if parsed.path == "/api/papers":
            self._send_json(self.monitor.cache)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh":
            self._send_json(self.monitor.refresh())
            return
        if parsed.path == "/api/config":
            payload = self._read_json()
            try:
                snapshot = self.monitor.update_config(payload)
            except (TypeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(snapshot)
            return
        if parsed.path == "/api/push-test":
            try:
                snapshot = self.monitor.send_test_push()
            except (TypeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self._send_json(snapshot)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._set_common_headers("application/json; charset=utf-8")
        self.end_headers()

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b"{}"
        if not raw_body:
            return {}
        return json.loads(raw_body.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._set_common_headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static(self, raw_path: str) -> None:
        route = raw_path or "/"
        if route == "/":
            route = "/index.html"
        target = (STATIC_DIR / route.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = target.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(target))
        self.send_response(HTTPStatus.OK)
        self._set_common_headers(mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _set_common_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ECG Paper Radar web app.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind the local server to.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the local server to.")
    parser.add_argument(
        "--refresh-now",
        action="store_true",
        help="Refresh the PubMed cache immediately and exit without starting the web server.",
    )
    parser.add_argument(
        "--push-test",
        action="store_true",
        help="Send a test WeChat push using the current local configuration and cache.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    monitor = PaperMonitor()

    if args.refresh_now:
        snapshot = monitor.refresh()
        cache = snapshot["cache"]
        print(
            json.dumps(
                {
                    "status": cache["status"],
                    "count": cache["count"],
                    "last_success_at": cache["last_success_at"],
                    "error": cache["error"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.push_test:
        snapshot = monitor.send_test_push()
        cache = snapshot["cache"]
        print(
            json.dumps(
                {
                    "push_status": cache["last_push_status"],
                    "push_message": cache["last_push_message"],
                    "push_error": cache["last_push_error"],
                    "push_count": cache["last_push_count"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    PaperRequestHandler.monitor = monitor
    server = ThreadingHTTPServer((args.host, args.port), PaperRequestHandler)
    monitor.start()
    print(f"ECG Paper Radar is running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop()
        server.server_close()


if __name__ == "__main__":
    main()
