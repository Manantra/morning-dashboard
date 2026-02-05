#!/usr/bin/env python3
"""Morning Dashboard for Daniel.

Sends a Telegram message every morning (via cron). Supports:
- Text-only dashboard (fallback)
- Image dashboard (portrait) rendered via Pillow when available

NOTE: This script sends via Telegram Bot API directly.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import urllib.request
from datetime import datetime
from typing import List, Tuple


TELEGRAM_CHAT_ID = "REDACTED_CHAT_ID"


def _load_telegram_bot_token() -> str:
    """Prefer OpenClaw config; fallback to env."""
    # 1) Env override
    env = os.environ.get("TELEGRAM_BOT_TOKEN")
    if env:
        return env

    # 2) OpenClaw config (authoritative)
    try:
        cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        token = cfg.get("channels", {}).get("telegram", {}).get("botToken")
        if token:
            return token
    except Exception:
        pass

    raise RuntimeError("Telegram bot token not found (env TELEGRAM_BOT_TOKEN or ~/.openclaw/openclaw.json).")


def _telegram_api_request(method: str, payload: dict) -> bool:
    token = _load_telegram_bot_token()
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    ok = bool(result.get("ok"))
    if not ok:
        print(f"❌ Telegram API error ({method}): {result}")
    return ok


def send_telegram_message(text: str) -> bool:
    """Send text via Telegram Bot API."""
    try:
        return _telegram_api_request(
            "sendMessage",
            {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        )
    except Exception as e:
        print(f"❌ sendMessage failed: {e}")
        return False


def send_telegram_photo(png_bytes: bytes, caption: str | None = None) -> bool:
    """Send PNG via Telegram Bot API (multipart/form-data)."""
    token = _load_telegram_bot_token()

    boundary = "----openclawboundary7MA4YWxkTrZu0gW"
    crlf = "\r\n"

    def _part(name: str, value: str) -> bytes:
        return (
            f"--{boundary}{crlf}"
            f"Content-Disposition: form-data; name=\"{name}\"{crlf}{crlf}"
            f"{value}{crlf}"
        ).encode("utf-8")

    body = bytearray()
    body += _part("chat_id", TELEGRAM_CHAT_ID)
    if caption:
        body += _part("caption", caption)

    # file part
    body += (
        f"--{boundary}{crlf}"
        f"Content-Disposition: form-data; name=\"photo\"; filename=\"dashboard.png\"{crlf}"
        f"Content-Type: image/png{crlf}{crlf}"
    ).encode("utf-8")
    body += png_bytes
    body += crlf.encode("utf-8")
    body += f"--{boundary}--{crlf}".encode("utf-8")

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        ok = bool(result.get("ok"))
        if not ok:
            print(f"❌ Telegram sendPhoto error: {result}")
        return ok
    except Exception as e:
        print(f"❌ sendPhoto failed: {e}")
        return False


def _wind_dir_label(deg: float) -> str:
    # 0=N, 90=E, 180=S, 270=W
    dirs = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
    try:
        idx = int((deg + 22.5) // 45) % 8
        return dirs[idx]
    except Exception:
        return "?"


def get_weather() -> dict:
    """Weather via Open-Meteo (no API key). Returns a structured dict.

    We avoid emoji in rendered text (some fonts show boxes). Icons are drawn
    in the dashboard image.
    """
    lat, lon = 52.60, 12.34  # Rathenow
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=Europe%2FBerlin"
    )

    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            cur = data.get("current") or {}
            daily = data.get("daily") or {}

            t = cur.get("temperature_2m")
            hum = cur.get("relative_humidity_2m")
            ws = cur.get("wind_speed_10m")
            wd = cur.get("wind_direction_10m")
            wcode = cur.get("weather_code")
            tmax = (daily.get("temperature_2m_max") or [None])[0]
            tmin = (daily.get("temperature_2m_min") or [None])[0]

            if t is None or tmax is None or tmin is None:
                raise ValueError("missing fields")

            return {
                "ok": True,
                "weather_code": wcode,
                "lines": [
                    ("temp", f"{t:.1f}°C aktuell"),
                    ("range", f"{tmin:.1f}°C / {tmax:.1f}°C"),  # Tief/Hoch kombiniert
                    ("humidity", f"{int(hum)}% Luftfeuchte") if hum is not None else None,
                    (
                        "wind",
                        f"{_wind_dir_label(float(wd))} {float(ws):.0f} km/h" if (ws is not None and wd is not None) else f"{float(ws):.0f} km/h",
                    )
                    if ws is not None
                    else None,
                ],
            }
        except Exception:
            continue

    return {"ok": False, "weather_code": None, "lines": [("info", "Wetterdaten nicht verfügbar")]}


def get_calendar() -> List[str]:
    """Events via khal."""
    try:
        result = subprocess.run(
            ["khal", "list", "today", "1d"],
            capture_output=True,
            text=True,
            timeout=12,
        )
        lines = result.stdout.strip().split("\n") if result.stdout else []
        events = [l for l in lines if not l.startswith("Today,") and l.strip()]
        return events if events else ["Keine Termine"]
    except Exception as e:
        return [f"Fehler: {e}"]


def get_todos_lines(max_lines: int = 6) -> List[str]:
    """Return today's todos as a clean list of task lines.

    We intentionally ignore headings/notes like:
    - Markdown headers ("#", "##")
    - Empty lines
    - Free-text intro lines

    We keep only checkbox tasks ("- [ ]" / "- [x]") to avoid rendering
    section titles like "Übernommen von gestern" as a todo item.
    """
    todo_file = f"/home/clawd/clawd/todos/{datetime.now().strftime('%Y-%m-%d')}.md"
    if not os.path.exists(todo_file):
        return ["Keine To-dos für heute"]

    try:
        with open(todo_file, "r", encoding="utf-8") as f:
            raw = f.read().splitlines()

        tasks: List[str] = []
        for ln in raw:
            s = ln.strip()
            if not s:
                continue
            if s.startswith("#"):
                continue
            # Only keep actual checkbox tasks
            if s.startswith("- ["):
                tasks.append(s)

        if not tasks:
            return ["Keine To-dos für heute"]

        if len(tasks) > max_lines:
            return tasks[:max_lines] + [f"… (+{len(tasks) - max_lines} weitere)"]
        return tasks
    except Exception:
        return ["Keine To-dos für heute"]


def get_upcoming_birthdays(days: int = 7) -> List[str]:
    """Get birthdays within the next N days, sorted by proximity.

    Returns formatted strings like:
    - "Heute: Valentina (26)"
    - "Morgen: Max (35)"
    - "in 3 Tagen: Anna (42)"
    """
    birthdays_file = "/home/clawd/clawd/data/people/birthdays.json"
    try:
        with open(birthdays_file, "r", encoding="utf-8") as f:
            birthdays = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    today = datetime.now().date()
    upcoming: List[Tuple[int, str, int | None]] = []  # (days_until, name, age)

    for name, data in birthdays.items():
        day = data.get("day")
        month = data.get("month")
        if not day or not month:
            continue

        # Calculate days until birthday
        try:
            birthday_this_year = today.replace(month=month, day=day)
        except ValueError:
            continue  # Invalid date

        if birthday_this_year < today:
            # Birthday already passed this year, check next year
            try:
                birthday_this_year = birthday_this_year.replace(year=today.year + 1)
            except ValueError:
                continue

        days_until = (birthday_this_year - today).days

        if days_until <= days:
            year_born = data.get("year")
            age = None
            if year_born:
                age = birthday_this_year.year - int(year_born)
            upcoming.append((days_until, name, age))

    # Sort by days until (closest first)
    upcoming.sort(key=lambda x: x[0])

    # Format results
    results: List[str] = []
    for days_until, name, age in upcoming:
        age_str = f" ({age})" if age else ""

        if days_until == 0:
            prefix = "Heute"
        elif days_until == 1:
            prefix = "Morgen"
        else:
            prefix = f"in {days_until} Tagen"

        results.append(f"{prefix}: {name}{age_str}")

    return results


# ------------------ Image rendering (Pillow) ------------------

def _find_font() -> str | None:
    candidates = [
        "/home/clawd/clawd/assets/fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/home/linuxbrew/.linuxbrew/share/fonts/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _wrap(draw, text: str, font, max_width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        trial = (" ".join(cur + [w])).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def render_dashboard_png(
    title: str,
    subtitle: str,
    weather: dict,
    calendar: List[str],
    todos: List[str],
    birthdays: List[str],
) -> bytes:
    # Import here so text-only fallback works without Pillow
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    W, H = 1080, 2340  # portrait (taller for modern iPhones ~19.5:9)

    style = os.environ.get("DASH_STYLE", "cards").strip().lower()  # cards|list
    theme = os.environ.get("DASH_THEME", "dark").strip().lower()   # dark|light
    icons = os.environ.get("DASH_ICONS", "on").strip().lower()     # on|off
    icons_on = icons not in {"off", "0", "false", "no"}

    if style not in {"cards", "list"}:
        style = "cards"
    if theme not in {"dark", "light"}:
        theme = "dark"

    # iOS-like palettes
    if theme == "light":
        bg = (242, 242, 247)          # iOS grouped background
        card = (255, 255, 255)
        card2 = (255, 255, 255)
        white = (18, 18, 20)          # primary text (near-black)
        muted = (90, 90, 100)
        divider = (199, 199, 204)
        shadow = (0, 0, 0)
        shadow_alpha = 35
    else:
        bg = (13, 16, 22)
        card = (24, 30, 42)
        card2 = (28, 36, 52)
        white = (245, 246, 250)
        muted = (175, 183, 196)
        divider = (55, 64, 82)
        shadow = (0, 0, 0)
        shadow_alpha = 70

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    font_path = _find_font()
    if not font_path:
        raise RuntimeError("No font found on system")

    f_title = ImageFont.truetype(font_path, 64)
    f_sub = ImageFont.truetype(font_path, 34)
    f_h = ImageFont.truetype(font_path, 40)
    f_txt = ImageFont.truetype(font_path, 32)

    margin = 48
    y = 210  # Zentriert für 2340px Höhe (extra ~200px oben für iPhone-Vollbild)

    # Header
    draw.text((margin, y), title, fill=white, font=f_title)
    y += 80
    draw.text((margin, y), subtitle, fill=muted, font=f_sub)
    y += 58

    # ---- icon helpers (drawn, not emoji) ----
    ICON = (230, 236, 245)
    ICON_MUTED = (160, 170, 190)

    def _weather_code_to_kind(code: int | None) -> str:
        # Open-Meteo weather codes: https://open-meteo.com/en/docs
        if code is None:
            return "cloud"
        try:
            c = int(code)
        except Exception:
            return "cloud"
        if c == 0:
            return "sun"
        if c in (1, 2, 3):
            return "cloud"
        if c in (45, 48):
            return "fog"
        if c in (51, 53, 55, 56, 57):
            return "drizzle"
        if c in (61, 63, 65, 66, 67):
            return "rain"
        if c in (71, 73, 75, 77):
            return "snow"
        if c in (80, 81, 82):
            return "rain"
        if c in (85, 86):
            return "snow"
        if c in (95, 96, 99):
            return "storm"
        return "cloud"

    def _icon_sun(size: int) -> "Image.Image":
        from PIL import Image
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        cx = cy = size // 2
        r = int(size * 0.22)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ICON, width=6)
        for a in range(0, 360, 45):
            import math
            rr1 = int(size * 0.34)
            rr2 = int(size * 0.46)
            x1 = cx + int(rr1 * math.cos(math.radians(a)))
            y1 = cy + int(rr1 * math.sin(math.radians(a)))
            x2 = cx + int(rr2 * math.cos(math.radians(a)))
            y2 = cy + int(rr2 * math.sin(math.radians(a)))
            d.line((x1, y1, x2, y2), fill=ICON, width=6)
        return im

    def _icon_cloud(size: int, rain: bool = False, snow: bool = False) -> "Image.Image":
        from PIL import Image
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        y = int(size * 0.46)
        d.ellipse((int(size * 0.18), y - int(size * 0.18), int(size * 0.44), y + int(size * 0.08)), outline=ICON, width=6)
        d.ellipse((int(size * 0.36), y - int(size * 0.26), int(size * 0.66), y + int(size * 0.08)), outline=ICON, width=6)
        d.ellipse((int(size * 0.56), y - int(size * 0.18), int(size * 0.82), y + int(size * 0.08)), outline=ICON, width=6)
        d.rounded_rectangle((int(size * 0.18), y, int(size * 0.82), int(size * 0.70)), radius=18, outline=ICON, width=6)
        if rain:
            for i in range(3):
                x = int(size * (0.30 + i * 0.18))
                d.line((x, int(size * 0.74), x - 10, int(size * 0.90)), fill=ICON_MUTED, width=6)
        if snow:
            for i in range(3):
                x = int(size * (0.30 + i * 0.18))
                d.text((x - 10, int(size * 0.73)), "*", fill=ICON_MUTED, font=f_h)
        return im

    def _icon_fog(size: int) -> "Image.Image":
        from PIL import Image
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for j in range(4):
            y = int(size * (0.30 + j * 0.14))
            d.rounded_rectangle((int(size * 0.14), y, int(size * 0.86), y + 10), radius=8, outline=ICON_MUTED, width=4)
        return im

    def _icon_thermo(size: int, updown: str = "") -> "Image.Image":
        from PIL import Image
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        # bulb
        d.ellipse((int(size*0.36), int(size*0.60), int(size*0.64), int(size*0.88)), outline=ICON, width=6)
        d.rounded_rectangle((int(size*0.46), int(size*0.18), int(size*0.54), int(size*0.70)), radius=10, outline=ICON, width=6)
        if updown == "up":
            d.polygon([(size*0.78, size*0.30), (size*0.90, size*0.48), (size*0.66, size*0.48)], outline=ICON_MUTED)
        elif updown == "down":
            d.polygon([(size*0.78, size*0.54), (size*0.90, size*0.36), (size*0.66, size*0.36)], outline=ICON_MUTED)
        return im

    def _icon_drop(size: int) -> "Image.Image":
        from PIL import Image
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.polygon([(size*0.50, size*0.18), (size*0.70, size*0.52), (size*0.50, size*0.86), (size*0.30, size*0.52)], outline=ICON, width=6)
        return im

    def _icon_wind(size: int) -> "Image.Image":
        from PIL import Image
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for j in range(3):
            y = int(size * (0.32 + j * 0.18))
            d.arc((int(size*0.10), y, int(size*0.90), y+int(size*0.30)), start=0, end=180, fill=ICON, width=6)
        return im

    def _pick_icon_for_weather(kind: str, size: int) -> "Image.Image":
        if kind == "sun":
            return _icon_sun(size)
        if kind in ("rain", "drizzle", "storm"):
            return _icon_cloud(size, rain=True)
        if kind == "snow":
            return _icon_cloud(size, snow=True)
        if kind == "fog":
            return _icon_fog(size)
        return _icon_cloud(size)

    def _pick_icon_for_weather_line(line_kind: str, size: int) -> "Image.Image":
        if line_kind == "temp":
            return _icon_thermo(size)
        if line_kind == "range":
            return _icon_thermo(size)  # Tief/Hoch range
        if line_kind == "humidity":
            return _icon_drop(size)
        if line_kind == "wind":
            return _icon_wind(size)
        return _icon_cloud(size)

    def _rounded_with_shadow(x: int, y: int, w: int, h: int, radius: int, fill_rgb: Tuple[int,int,int]):
        """Draw a rounded rect with subtle shadow (Apple-ish)."""
        if style == "cards":
            # shadow layer
            from PIL import Image
            shadow_layer = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow_layer)
            sd.rounded_rectangle([20, 20, 20 + w, 20 + h], radius=radius, fill=(*shadow, shadow_alpha))
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(18))
            img.paste(shadow_layer, (x - 20, y - 10), shadow_layer)

        # card itself
        draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill_rgb)

    def card_block(x: int, y: int, w: int, h: int, header: str, lines: List[str], bgcol, *, icon_mode: str = "none") -> int:
        r = 34 if style == "cards" else 20
        if style == "cards":
            _rounded_with_shadow(x, y, w, h, r, bgcol)
        else:
            # list/grouped: subtle background, minimal card feel
            _rounded_with_shadow(x, y, w, h, r, bgcol)

        draw.text((x + 34, y + 26), header, fill=white, font=f_h)

        yy = y + 86
        max_w = w - 68

        def _clean_todo(s: str) -> str:
            s = (s or "").strip()
            for prefix in ("- [ ] ", "- [x] ", "- [X] "):
                if s.startswith(prefix):
                    s = s[len(prefix):]
                    break
            if s.startswith("-"):
                s = s[1:].strip()
            return s

        _h = (header or "").strip().lower()
        is_todos = _h in {"to-dos", "to‑dos", "to-do", "to‑do", "todos", "to dos"}
        is_calendar = _h in {"termine", "kalender", "calendar", "events"}

        for i, ln in enumerate(lines):
            if is_todos:
                ln = "• " + _clean_todo(ln)

            base_x = x + 34

            # 1) Hanging indent for bullet lines so wrapped text aligns nicely.
            if ln.startswith("• "):
                prefix = "• "
                bullet_w = draw.textbbox((0, 0), prefix, font=f_txt)[2]
                content = ln[len(prefix):].lstrip()
                wrapped_lines = _wrap(draw, content, f_txt, max_w - bullet_w)
                for j, wrapped in enumerate(wrapped_lines):
                    if j == 0:
                        draw.text((base_x, yy), prefix + wrapped, fill=muted, font=f_txt)
                    else:
                        draw.text((base_x + bullet_w, yy), wrapped, fill=muted, font=f_txt)
                    yy += 44
                    if yy > y + h - 40:
                        return yy

            # 2) Calendar-style hanging indent: keep the time aligned, indent wrapped lines after it.
            elif is_calendar and ln.strip() and ":" in ln.split(maxsplit=1)[0]:
                first = ln.strip().split(maxsplit=1)
                if len(first) == 2 and len(first[0]) in (4, 5) and first[0].count(":") == 1:
                    time_prefix = first[0] + " "
                    content = first[1].strip()
                    time_w = draw.textbbox((0, 0), time_prefix, font=f_txt)[2]
                    wrapped_lines = _wrap(draw, content, f_txt, max_w - time_w)
                    for j, wrapped in enumerate(wrapped_lines):
                        if j == 0:
                            draw.text((base_x, yy), time_prefix + wrapped, fill=muted, font=f_txt)
                        else:
                            draw.text((base_x + time_w, yy), wrapped, fill=muted, font=f_txt)
                        yy += 44
                        if yy > y + h - 40:
                            return yy
                else:
                    wrapped_lines = _wrap(draw, ln, f_txt, max_w)
                    for wrapped in wrapped_lines:
                        draw.text((base_x, yy), wrapped, fill=muted, font=f_txt)
                        yy += 44
                        if yy > y + h - 40:
                            return yy

            # 3) Default wrap (weather, birthdays, etc.)
            else:
                wrapped_lines = _wrap(draw, ln, f_txt, max_w)
                for wrapped in wrapped_lines:
                    draw.text((base_x, yy), wrapped, fill=muted, font=f_txt)
                    yy += 44
                    if yy > y + h - 40:
                        return yy

            # dividers
            if yy < y + h - 60:
                line_y = yy + 8
                # for list style: always divider between entries; for cards: only for todos
                if style == "list" or is_todos:
                    draw.line((x + 34, line_y, x + w - 34, line_y), fill=divider, width=2)
                    yy += 24

        return yy

    # Layout: 4 vertical sections (stacked) for more room (esp. To-dos)
    gap = 22
    card_w = W - 2 * margin

    # Heights tuned for original 1920px content area (unabhängig von Canvas-Größe)
    h_weather = 280  # Kompakter da Höchst/Tiefst jetzt kombiniert
    h_calendar = 360
    h_birthdays = 300  # Etwas größer für 7-day preview (war 260)
    # Fixe Inhaltshöhe wie bei 1920px Canvas (1920 - 44 top - 40 bottom = 1836)
    content_height = 1836
    remaining = content_height - (h_weather + h_calendar + h_birthdays + 3 * gap)
    h_todos = max(520, remaining)

    # Weather icon mode: "mini" (one big icon) or "lines" (icon per line)
    weather_icon_mode = os.environ.get("DASH_WEATHER_ICON_MODE", "mini").strip().lower()
    if weather_icon_mode not in {"mini", "lines"}:
        weather_icon_mode = "mini"

    def weather_block(x: int, y0: int, w: int, h: int, weather_obj: dict) -> None:
        r = 34 if style == "cards" else 20
        _rounded_with_shadow(x, y0, w, h, r, card)
        draw.text((x + 34, y0 + 26), "Wetter", fill=white, font=f_h)

        # Big icon (top-right, etwas höher damit nicht auf Trennlinie)
        if icons_on:
            kind = _weather_code_to_kind(weather_obj.get("weather_code"))
            big = _pick_icon_for_weather(kind, 140)
            img.paste(big, (x + w - 34 - 140, y0 + 6), big)

        yy = y0 + 92
        max_w = w - 68

        # Build tidy rows (label + value) instead of free-form strings.
        label_map = {
            "temp": "Aktuell",
            "range": "Tief / Hoch",
            "humidity": "Luftfeuchte",
            "wind": "Wind",
            "info": "Info",
        }

        def _extract_value(lk: str, txt: str) -> str:
            t = (txt or "").strip()
            if not t:
                return t
            parts = t.split()
            # common patterns from get_weather():
            # "-0.2°C aktuell" → value=-0.2°C
            # "1.5°C / 8.2°C"  → keep full range
            # "92% Luftfeuchte"→ value=92%
            if lk == "range":
                return t  # keep full "X°C / Y°C"
            if lk in {"temp", "humidity"} and parts:
                return parts[0]
            return t

        rows = []
        for (lk, txt) in [t for t in (weather_obj.get("lines") or []) if t]:
            rows.append((lk, label_map.get(lk, lk), _extract_value(lk, txt)))

        # Column layout
        use_line_icon = icons_on and (weather_icon_mode == "lines")
        icon_w = 54 if use_line_icon else 0
        label_w = 240  # fixed label column for a clean look
        col_gap = 16

        x0 = x + 34
        label_x = x0 + icon_w
        value_x = label_x + label_w + col_gap
        value_w = max_w - icon_w - label_w - col_gap

        for idx, (lk, label, value) in enumerate(rows):
            if use_line_icon:
                ic = _pick_icon_for_weather_line(lk, 44)
                img.paste(ic, (x0, yy - 6), ic)

            # label (muted)
            draw.text((label_x, yy), label, fill=muted, font=f_txt)

            # value (brighter, right column)
            for j, wrapped in enumerate(_wrap(draw, value, f_txt, value_w)):
                draw.text((value_x, yy + j * 44), wrapped, fill=white, font=f_txt)
                if yy + (j + 1) * 44 > y0 + h - 40:
                    break

            yy += 44
            if yy > y0 + h - 40:
                break

            # subtle dividers (helps "aufgeräumt")
            if yy < y0 + h - 60:
                draw.line((x + 34, yy + 6, x + w - 34, yy + 6), fill=divider, width=2)
                yy += 18

    y0 = y
    weather_block(margin, y0, card_w, h_weather, weather)

    y0 = y0 + h_weather + gap
    card_block(margin, y0, card_w, h_calendar, "Termine", calendar[:11], card)

    y0 = y0 + h_calendar + gap
    # more space for todos
    card_block(margin, y0, card_w, h_todos, "To-dos", todos[:18], card2)

    y0 = y0 + h_todos + gap
    b_lines = birthdays if birthdays else ["Keine in den nächsten 7 Tagen"]
    card_block(margin, y0, card_w, h_birthdays, "Geburtstage (7 Tage)", b_lines[:5], card2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ------------------ Main ------------------

def get_greeting() -> str:
    """Return time-appropriate greeting."""
    hour = datetime.now().hour
    if 5 <= hour < 11:
        return "Guten Morgen!"
    elif 11 <= hour < 14:
        return "Mahlzeit!"
    elif 14 <= hour < 18:
        return "Guten Tag!"
    elif 18 <= hour < 22:
        return "Guten Abend!"
    else:
        return "Gute Nacht!"


def build_text_dashboard(today_str: str, greeting: str, weather_lines: List[str], birthdays: List[str], events: List[str], todos: List[str]) -> str:
    lines: List[str] = []
    lines.append(f"{greeting.replace('!', '')}, Daniel!")
    lines.append("")
    lines.append(f"Rathenow – {today_str}")
    lines.append("")

    lines.append("🌡️ Wetter:")
    for ln in weather_lines:
        lines.append(f"- {ln}")
    lines.append("")

    if birthdays:
        lines.append("🎂 Geburtstage (nächste 7 Tage):")
        for b in birthdays:
            lines.append(f"  {b}")
        lines.append("")

    lines.append("📅 Termine heute:")
    for e in events:
        lines.append(f"- {e}")
    lines.append("")

    lines.append("📝 To-dos:")
    for t in todos:
        lines.append(f"- {t}")

    return "\n".join(lines)


def main() -> int:
    today = datetime.now()
    today_str = today.strftime("%d.%m.%Y")
    greeting = get_greeting()

    weather = get_weather()
    weather_lines = [x for x in (weather.get("lines") or []) if x]
    events = get_calendar()
    todos = get_todos_lines()
    birthdays = get_upcoming_birthdays(days=7)

    # 1) Try image dashboard
    try:
        png = render_dashboard_png(
            title=greeting,
            subtitle=f"Rathenow · {today_str}",
            weather=weather,
            calendar=events,
            todos=todos,
            birthdays=birthdays,
        )
        ok_img = send_telegram_photo(png, caption=None)
        if ok_img:
            # Text-Version nur auf Wunsch (Daniel-Präferenz). Aktuell NICHT automatisch senden.
            print("✅ Image dashboard sent")
            return 0
    except Exception as e:
        print(f"⚠️ Image dashboard failed, falling back to text: {e}")

    # 2) Fallback: text only
    message = build_text_dashboard(today_str, greeting, weather_lines, birthdays, events, todos)
    if send_telegram_message(message):
        print("✅ Text dashboard sent")
        return 0

    print("❌ Sending failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
