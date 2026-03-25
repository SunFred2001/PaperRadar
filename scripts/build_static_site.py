from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app

STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
DASHBOARD_PATH = DATA_DIR / "dashboard.json"
SENSITIVE_KEYS = {"serverchan_sendkey", "wecom_webhook_url"}


class WorkflowMonitor(app.PaperMonitor):
    def __init__(self, runtime_overrides: dict[str, Any]) -> None:
        self.runtime_overrides = runtime_overrides
        super().__init__()

    def _load_config(self) -> dict[str, Any]:
        config = super()._load_config()
        config.update(self.runtime_overrides)
        return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the static Paper Radar site.")
    parser.add_argument("--site-dir", default="site", help="Directory where the static site will be written.")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force a live PubMed refresh before building the site.",
    )
    parser.add_argument(
        "--send-daily-push",
        action="store_true",
        help="If the configured time has arrived, send the daily WeChat push before building the site.",
    )
    return parser.parse_args()


def env_overrides() -> dict[str, Any]:
    mapping: dict[str, Any] = {}

    string_fields = {
        "CONTACT_EMAIL": "contact_email",
        "PUSH_CHANNEL": "push_channel",
        "PUSH_TIME": "push_time",
        "PUSH_TIMEZONE": "push_timezone",
        "SERVERCHAN_SENDKEY": "serverchan_sendkey",
        "WECOM_WEBHOOK_URL": "wecom_webhook_url",
    }
    for env_key, config_key in string_fields.items():
        value = os.getenv(env_key, "").strip()
        if value:
            mapping[config_key] = value

    return mapping


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    sanitized = {key: value for key, value in config.items() if key not in SENSITIVE_KEYS}
    sanitized["serverchan_sendkey"] = ""
    sanitized["wecom_webhook_url"] = ""
    return sanitized


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = dict(snapshot)
    payload["config"] = public_config(snapshot["config"])
    return payload


def write_dashboard(snapshot: dict[str, Any]) -> None:
    DASHBOARD_PATH.write_text(
        json.dumps(public_snapshot(snapshot), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def copy_tree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def build_site(site_dir: Path) -> None:
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True, exist_ok=True)

    copy_tree_contents(STATIC_DIR, site_dir)
    site_data_dir = site_dir / "data"
    site_data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DATA_DIR / "papers_cache.json", site_data_dir / "papers_cache.json")
    shutil.copy2(DASHBOARD_PATH, site_data_dir / "dashboard.json")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")


def main() -> None:
    args = parse_args()
    monitor = WorkflowMonitor(env_overrides())

    if args.send_daily_push and monitor._push_due():
        monitor._run_daily_push()
    elif args.force_refresh or monitor._refresh_due():
        monitor.refresh()

    snapshot = monitor.snapshot()
    write_dashboard(snapshot)
    build_site((ROOT / args.site_dir).resolve())

    summary = {
        "status": snapshot["cache"]["status"],
        "count": snapshot["cache"]["count"],
        "last_success_at": snapshot["cache"]["last_success_at"],
        "push_status": snapshot["cache"]["last_push_status"],
        "next_push_at": snapshot["next_push_at"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
