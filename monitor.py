#!/usr/bin/env python3
"""Monitor a public IP address and a Home Assistant API endpoint.

Uses only the Python standard library. Configuration is read from .env.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "monitor.log"


def load_env() -> None:
    """Load KEY=VALUE settings without overriding existing process variables."""
    if not ENV_FILE.is_file():
        raise RuntimeError(f"Configuration file not found: {ENV_FILE}")
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required .env setting: {name}")
    return value


def positive_int(name: str) -> int:
    raw = required(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero")
    return value


load_env()

HOME_IP = required("HOME_IP")
HA_URL = required("HA_URL")
TG_TOKEN = required("TELEGRAM_TOKEN")
TG_CHAT_ID = required("TELEGRAM_CHAT_ID")
HA_TOKEN = required("HA_TOKEN")

INTERVAL = positive_int("CHECK_INTERVAL")
PING_TIMEOUT = positive_int("PING_TIMEOUT")
HA_TIMEOUT = positive_int("HA_TIMEOUT")
TELEGRAM_TIMEOUT = positive_int("TELEGRAM_TIMEOUT")
FAIL_THRESHOLD = positive_int("FAIL_THRESHOLD")
RECOVERY_THRESHOLD = positive_int("RECOVERY_THRESHOLD")

def parse_bool(name: str) -> bool:
    raw = required(name).lower()
    if raw not in {"true", "false", "1", "0", "yes", "no"}:
        raise RuntimeError(f"{name} must be true or false")
    return raw in {"true", "1", "yes"}


DAILY_REPORT_ENABLED = parse_bool("DAILY_REPORT_ENABLED")
DAILY_REPORT_TIME = required("DAILY_REPORT_TIME")
try:
    datetime.strptime(DAILY_REPORT_TIME, "%H:%M")
except ValueError as exc:
    raise RuntimeError("DAILY_REPORT_TIME must use HH:MM format") from exc

TIMEZONE_NAME = required("TIMEZONE")
try:
    TZ = ZoneInfo(TIMEZONE_NAME)
except Exception as exc:
    raise RuntimeError(f"Invalid time zone: {TIMEZONE_NAME}") from exc

if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}", HOME_IP):
    raise RuntimeError("HOME_IP must be an IPv4 address")
if not HA_URL.startswith(("https://", "http://")):
    raise RuntimeError("HA_URL must start with http:// or https://")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def timestamp() -> str:
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M:%S %Z")


def check_ping() -> bool:
    """Return True when HOME_IP answers one ICMP echo request."""
    try:
        result = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", str(PING_TIMEOUT), HOME_IP],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PING_TIMEOUT + 2,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_home_assistant() -> tuple[bool, str | None]:
    """Check HA's authenticated /api/config endpoint and read its version."""
    request = urllib.request.Request(
        HA_URL,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HA_TIMEOUT) as response:
            if response.status != 200:
                return False, None
            data = json.loads(response.read().decode("utf-8"))
            version = data.get("version")
            if not isinstance(version, str) or not version:
                return False, None
            return True, version
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False, None


def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps(
        {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "HTML"}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and result.get("ok") is True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logging.error("Telegram delivery failed: %s", exc)
        return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                state.setdefault("ping", None)
                state.setdefault("ha", None)
                state.setdefault("daily_report_date", None)
                return state
        except (OSError, json.JSONDecodeError):
            logging.exception("Cannot read state file; starting with unknown state")
    return {"ping": None, "ha": None, "daily_report_date": None}


def save_state(state: dict) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def maybe_send_daily_report(
    state: dict, ping_ok: bool, ha_ok: bool, ha_version: str | None
) -> None:
    if not DAILY_REPORT_ENABLED:
        return
    now = datetime.now(TZ)
    today = now.date().isoformat()
    if now.strftime("%H:%M") < DAILY_REPORT_TIME:
        return
    if state.get("daily_report_date") == today:
        return

    lines = [
        "📊 <b>Ежедневный отчёт мониторинга</b>",
        "🟢 Мониторинг активен",
        f"🌐 Контрольный IP ({escape(HOME_IP)}): "
        f"{'доступен' if ping_ok else 'недоступен'}",
        f"🏠 Home Assistant: {'работает' if ha_ok else 'недоступен'}",
    ]
    if ha_version:
        lines.append(f"Версия HA: {escape(ha_version)}")
    lines.append(f"🕒 Последняя проверка: {timestamp()}")

    if send_telegram("\n".join(lines)):
        state["daily_report_date"] = today
        save_state(state)
        logging.info("Daily report delivered for %s", today)
    else:
        logging.error("Daily report pending; will retry next cycle")


def main() -> None:
    state = load_state()
    counters = {
        "ping_fail": 0,
        "ping_ok": 0,
        "ha_fail": 0,
        "ha_ok": 0,
    }

    logging.info("Home Monitor started")
    logging.info("Monitoring IP: %s", HOME_IP)
    logging.info("Monitoring Home Assistant: %s", HA_URL)

    first_check = state["ping"] is None or state["ha"] is None

    while True:
        cycle_start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            ping_future = executor.submit(check_ping)
            ha_future = executor.submit(check_home_assistant)
            ping_ok = ping_future.result()
            ha_ok, ha_version = ha_future.result()

        logging.info(
            "Check: Ping=%s, HA=%s%s",
            "OK" if ping_ok else "FAIL",
            "OK" if ha_ok else "FAIL",
            f" (version {ha_version})" if ha_ok else "",
        )

        checks = {"ping": ping_ok, "ha": ha_ok}
        for name, result in checks.items():
            if result:
                counters[f"{name}_fail"] = 0
                counters[f"{name}_ok"] += 1
            else:
                counters[f"{name}_ok"] = 0
                counters[f"{name}_fail"] += 1

        messages: list[str] = []
        updates: dict[str, bool] = {}

        if first_check:
            messages.extend(
                [
                    "🟢 <b>Мониторинг запущен</b>",
                    f"🌐 Контрольный IP: {'доступен' if ping_ok else 'недоступен'}",
                    f"🏠 Home Assistant: {'работает' if ha_ok else 'недоступен'}",
                ]
            )
            if ha_version:
                messages.append(f"Версия HA: {escape(ha_version)}")
            updates = {"ping": ping_ok, "ha": ha_ok}
        else:
            for name, result in checks.items():
                previous = state.get(name)
                if previous is None:
                    updates[name] = result
                    continue
                if result == previous:
                    continue

                confirmed = (
                    counters[f"{name}_ok"] >= RECOVERY_THRESHOLD
                    if result
                    else counters[f"{name}_fail"] >= FAIL_THRESHOLD
                )
                if not confirmed:
                    continue

                service_name = (
                    "Контрольный IP / домашний шлюз"
                    if name == "ping"
                    else "Home Assistant"
                )
                messages.append(
                    f"{'🟢 <b>Восстановлено' if result else '🔴 <b>Недоступен'}: "
                    f"{service_name}</b>"
                )
                updates[name] = result

        if messages:
            message = "\n".join(messages) + f"\n\n🕒 {timestamp()}"
            if send_telegram(message):
                state.update(updates)
                save_state(state)
                first_check = False
                logging.info("State notification delivered")
            else:
                logging.error("Notification pending; will retry next cycle")

        maybe_send_daily_report(state, ping_ok, ha_ok, ha_version)

        elapsed = time.monotonic() - cycle_start
        time.sleep(max(0, INTERVAL - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Home Monitor stopped")
    except Exception:
        logging.exception("Fatal monitor error")
        raise
