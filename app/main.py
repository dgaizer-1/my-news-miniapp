# main.py
from __future__ import annotations

import os
import time
import html
import random
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# === чтобы TMDB_API_KEY подтянулся из .env ===
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

APP_TITLE = "Моя подборка"
MSK_TZ = timezone(timedelta(hours=3))
# ВРЕМЯ ЖИЗНИ КЭША ДЛЯ КАЖДОЙ ТЕМЫ
DEFAULT_TTL = 15 * 60  # 15 минут по умолчанию

TOPIC_TTL = {
    "afisha": 2 * 60 * 60,   # 2 часа
    "series": 24 * 60 * 60,  # 24 часа
    "movies": 24 * 60 * 60,  # 24 часа
    "agro":   2 * 60 * 60,   # 2 часа
    "svo":    10 * 60,       # 10 минут
    "ai":     60 * 60,       # 1 час
}

def get_ttl(topic: str) -> int:
    return TOPIC_TTL.get(topic, DEFAULT_TTL)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()  # TMDB v3 API key

# Фиксированные картинки тем (одна на тему)
TOPIC_IMAGES = {
    "afisha": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?q=80&w=1200&auto=format&fit=crop",
    "series": "https://images.unsplash.com/photo-1585951237318-9ea5e175b891?q=80&w=1200&auto=format&fit=crop",
    "movies": "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?q=80&w=1200&auto=format&fit=crop",
    "agro": "https://images.unsplash.com/photo-1464226184884-fa280b87c399?q=80&w=1200&auto=format&fit=crop",
    "svo": "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?q=80&w=1200&auto=format&fit=crop",  # нейтральный пейзаж
    "ai": "https://images.unsplash.com/photo-1518770660439-4636190af475?q=80&w=1200&auto=format&fit=crop",
}

# Telegram-каналы (без @ / t.me/)
SVO_TELEGRAM = ["bloodysx", "bbbreaking", "Alexey_Pivo_varov", "mash"]
AFISHA_TELEGRAM = ["sysoevfm", "instafoodpassion"]
AGRO_TELEGRAM = ["svoe_fermerstvo", "agro_nomika", "agroinvestor", "mcxae", "mcx_ru"]

# Кэш в памяти
CACHE: Dict[str, Dict[str, Any]] = {}

app = FastAPI(title=APP_TITLE)

app.mount("/static", StaticFiles(directory="static"), name="static")
# Убираем браузерное предупреждение ngrok
@app.middleware("http")
async def add_skip_warning_header(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["ngrok-skip-browser-warning"] = "true"
    return resp

# Простой healthcheck (для curl)
@app.get("/health", response_class=PlainTextResponse)
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")

def now_ts() -> int:
    return int(time.time())


def cache_get(topic: str) -> List[Dict[str, Any]] | None:
    rec = CACHE.get(topic)
    if not rec:
        return None
    ttl = rec.get("ttl") or get_ttl(topic)
    ts = rec.get("ts", 0)
    if now_ts() - ts > ttl:
        return None
    return rec.get("items") or None


def cache_set(topic: str, items: List[Dict[str, Any]]):
    CACHE[topic] = {
        "ts": now_ts(),
        "ttl": get_ttl(topic),
        "items": items,
    }

def short(txt: str, limit: int = 240) -> str:
    t = " ".join((txt or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"

async def fetch_json(client: httpx.AsyncClient, url: str, **params) -> Dict[str, Any] | None:
    try:
        r = await client.get(url, params=params, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

async def fetch_html(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None

def parse_tg_list(html_text: str, base_url: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not html_text:
        return out
    soup = BeautifulSoup(html_text, "html.parser")
    wraps = soup.select(".tgme_widget_message_wrap")
    for w in wraps:
        txt_tag = w.select_one(".tgme_widget_message_text")
        text = txt_tag.get_text(" ", strip=True) if txt_tag else ""
        if not text:
            continue
        ts = 0
        t_tag = w.select_one("time")
        if t_tag and t_tag.has_attr("datetime"):
            try:
                dt = datetime.fromisoformat(t_tag["datetime"].replace("Z", "+00:00"))
                ts = int(dt.timestamp())
            except Exception:
                ts = 0
        link = base_url
        a_tag = txt_tag.select_one("a[href]") if txt_tag else None
        if a_tag and a_tag["href"].startswith(("http://", "https://")):
            link = a_tag["href"]
        img = ""
        p = w.select_one("a.tgme_widget_message_photo_wrap, a.tgme_widget_message_video_thumb")
        if p and p.has_attr("style"):
            st = p["style"]
            if "url(" in st:
                start = st.find("url(") + 4
                end = st.find(")", start)
                candidate = st[start:end].strip("'\"")
                if candidate.startswith("http"):
                    img = candidate
        out.append({
            "title": short(text, 120),
            "summary": short(text, 320),
            "url": link,
            "image": img,
            "ts": ts
        })
    return out

async def get_telegram_news(channels: List[str], limit_per_channel: int = 4, total_limit: int = 10) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for ch in channels:
            page = await fetch_html(client, f"https://t.me/s/{ch}")
            parsed = parse_tg_list(page or "", f"https://t.me/{ch}")
            if parsed:
                items.extend(parsed[:limit_per_channel])
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    for it in items:
        it.pop("ts", None)
    return items[:total_limit]


def _daily_seed(salt: str = "") -> str:
    # детерминированная соль на текущий день (MSK)
    return f"{datetime.now(MSK_TZ):%Y-%m-%d}-{salt}"


def _is_allowed_event(title: str) -> bool:
    """Фильтруем афишу:
       - оставляем выставки/концерты/театр/фестивали и т.п.
       - кино/фильмы оставляем только если упомянута 'премьера'
    """
    t = (title or "").lower()

    allowed_any = [
        "выставка", "экспозиция", "вернисаж",
        "концерт", "фестиваль", "джаз", "рок", "оркестр", "симфонический",
        "спектакль", "театр", "перформанс", "опера", "балет",
        "ярмарка", "маркет", "экскурсия",
        "лекция", "мастер-класс", "воркшоп", "презентация",
        "stand-up", "стендап", "open mic", "опен майк"
    ]
    if any(k in t for k in allowed_any):
        return True

    # кино/фильм — только если есть 'премьера'
    if ("кино" in t or "фильм" in t) and "премьера" in t:
        return True

    return False

# ====== AGRO helpers + сборщик ======

def _dedupe_by_url_title(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    for it in items:
        u = (it.get("url") or "").strip().lower()
        t = (it.get("title") or "").strip().lower()
        key = (u, t)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

def _agro_keep(title: str) -> bool:
    """Фильтр 'бизнес‑повестки' для агро."""
    t = (title or "").lower()

    allow = [
        "рынок", "экспорт", "импорт", "квота", "господдерж", "субсид",
        "урожай", "посев", "сбор", "засуха", "погода", "прогноз", "гкт",
        "цена", "подорожан", "подешев", "индекс", "инфляц",
        "зерн", "масл", "молок", "мяс", "скот", "птиц", "сахар",
        "логист", "порт", "жд", "перевалк", "экспортная пошлина",
        "техника", "технолог", "дрон", "агротех", "инвестици", "проект",
        "мсх", "минсельхоз", "постановлен", "приказ", "ФОТ", "меры поддержки"
    ]
    deny = [
        "рецепт", "как посадить", "огород", "дача", "садовод",
        "лайфхак", "маринад", "кулинар", "подкормк", "удобрение своими",
        "домашн", "рассада", "цветок", "комнатн"
    ]

    if any(x in t for x in deny):
        return False
    return any(x in t for x in allow)

async def get_agro(limit: int = 10) -> List[Dict[str, Any]]:
    """Новости агро: сайты + Telegram, фильтр бизнес-повестки, дедуп, дневная рандомизация."""
    items: List[Dict[str, Any]] = []

    # --- 1) Профильные сайты ---
    sources = [
        "https://www.agroinvestor.ru/news/",
        "https://www.agroxxi.ru/novosti.html",
        "https://mcx.gov.ru/press-service/news/",
    ]
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            for url in sources:
                html_text = await fetch_html(client, url)
                if not html_text:
                    continue
                soup = BeautifulSoup(html_text, "html.parser")
                for a in soup.select("a[href]")[:120]:
                    title = a.get_text(" ", strip=True)
                    href = (a.get("href") or "").strip()
                    if not title or len(title) < 12:
                        continue
                    if not _agro_keep(title):
                        continue
                    # абсолютная ссылка
                    if href.startswith("/"):
                        from urllib.parse import urlparse, urljoin
                        base = urlparse(url)
                        href = urljoin(f"{base.scheme}://{base.netloc}", href)
                    if not href.startswith("http"):
                        continue
                    items.append({
                        "title": short(title, 120),
                        "summary": "",
                        "url": href,
                        "image": "",
                        "_src": "site",
                    })
    except Exception:
        pass

    # --- 2) Telegram-каналы (существующий список AGRO_TELEGRAM) ---
    try:
        tg = await get_telegram_news(AGRO_TELEGRAM, limit_per_channel=3, total_limit=15)
        for it in tg:
            title = (it.get("title") or "").strip()
            if title and _agro_keep(title):
                items.append({
                    "title": short(title, 120),
                    "summary": short(it.get("summary") or "", 220),
                    "url": it.get("url") or "",
                    "image": it.get("image") or "",
                    "_src": "tg",
                })
    except Exception:
        pass

    # Дедуп + дневная рандомизация + лимит
    items = _dedupe_by_url_title(items)
    random.Random(_daily_seed("agro")).shuffle(items)
    return items[:limit]

# ================== SVO (телеграм + фильтр + дедуп) ===================
def _svo_keep(text: str) -> bool:
    """Фильтр СВО: мягче. Пускаем Путин/переговоры, иначе нужна связка из 2 групп."""
    t = (text or "").lower()

    # ❌ чёрный список (не про войну)
    deny = [
        "блогер", "шоумен", "актёр", "актрис", "певиц", "певец",
        "шоу", "концерт", "сериал", "кино", "премьера", "фильм",
        "ивент", "селеб", "звезда", "скандал",
        "бизнес", "компания", "акции", "крипт", "магазин", "мода"
    ]
    if any(x in t for x in deny):
        return False

    # ✅ маркеры темы
    core = [
        "сво", "спецоперац", "лбс", "фронт", "военн", "сводк", "минобороны",
        "всу", "зсу", "бригада", "батальон", "полк",
        "артилл", "мином", "пво", "бпла", "дрон", "шахед", "герань",
        "ракет", "танк", "бронетех", "боеприпас", "окоп", "инженерн",
        "сша", "америка", "зеленский", "путин", "переговор"
    ]
    actions = [
        "обстрел", "удар", "штурм", "рейд", "наступ", "контрнаступ",
        "прорыв", "оборона", "сбит", "подрыв", "взорван",
        "эвакуац", "переброс", "задержан", "зачистк", "высадк"
    ]
    places = [
        "бахмут", "артёмовск", "авдеев", "купянск", "лиман", "сватово",
        "угледар", "запорож", "херсон", "донецк", "луганск",
        "кременн", "часов яр", "марьинк", "работин", "харков", "харьков"
    ]

    # Явные пропуски
    if "путин" in t or "переговор" in t:
        return True

    has_core = any(k in t for k in core)
    has_actions = any(k in t for k in actions)
    has_places = any(k in t for k in places)

    # достаточно 2 из 3 групп, либо core + (actions|places)
    score = sum([has_core, has_actions, has_places])
    return score >= 2 and (has_core or has_actions)


async def get_svo(limit: int = 10) -> List[Dict[str, Any]]:
    """Новости СВО из телеграм-каналов + жёсткий фильтр и дедупликация."""
    raw = await get_telegram_news(
        SVO_TELEGRAM,
        limit_per_channel=6,   # берём побольше сырья
        total_limit=40
    )

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for it in raw:  # уже отсортировано по времени внутри get_telegram_news
        title = (it.get("title") or "").strip()
        if not title or not _svo_keep(title):
            continue

        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "title": short(title, 160),
            "summary": short(it.get("summary") or "", 240),
            "url": it.get("url") or "",
            "image": it.get("image") or "",
        })

        if len(out) >= limit:
            break

    return out

# ================== AI (новости, строгий фильтр + дедуп) ===================
async def get_ai_news(limit: int = 10) -> List[Dict[str, Any]]:
    """AI-лента: берём заголовки, фильтруем по ключевым словам, дедупим и нормализуем ссылки."""
    sites = [
        "https://techcrunch.com/tag/artificial-intelligence/",
        "https://venturebeat.com/category/ai/",
        "https://www.technologyreview.com/topic/artificial-intelligence/",
        "https://www.theverge.com/artificial-intelligence",
        "https://openai.com/blog/",
        "https://vc.ru/ai",
        "https://rb.ru/tag/iskusstvennyy-intellekt/",
        "https://www.computerra.ru/tag/iskusstvennyj-intellekt/",
    ]

    # ужесточённые ключевые слова (ядро)
    kw_core = [
        "ai", "искусственный интеллект", "нейросет", "llm", "gpt", "genai",
        "модель", "foundation model", "трансформер", "r1", "mistral", "llama",
        "distillation", "fine-tuning", "inference", "rag", "agent"
    ]
    # «сигнальные» маркеры (релизы/исследования/веса/opensource и т.п.)
    kw_signal = [
        "релиз", "запуск", "announc", "update", "обновлен", "weights",
        "research", "study", "paper", "benchmark", "sota",
        "open source", "opensource", "github", "репозитор", "датасет"
    ]

    out: List[Dict[str, Any]] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()

    def good(title: str) -> bool:
        t = (title or "").lower()
        if not any(k in t for k in kw_core):
            return False
        # усиливаем материалы с сигналами
        if any(k in t for k in kw_signal):
            return True
        # и пропускаем явно «про модели»
        return any(k in t for k in ["model", "модель", "llm", "gpt", "mistral", "llama", "r1"])

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        for url in sites:
            page = await fetch_html(client, url)
            if not page:
                continue
            soup = BeautifulSoup(page, "html.parser")
            for a in soup.select("a[href]")[:120]:
                title = a.get_text(" ", strip=True)
                href = (a.get("href") or "").strip()
                if not title or len(title) < 20:
                    continue
                if not good(title):
                    continue

                # нормализация относительных ссылок
                if href.startswith("/"):
                    from urllib.parse import urlparse, urljoin
                    base = urlparse(url)
                    href = urljoin(f"{base.scheme}://{base.netloc}", href)
                if not href.startswith("http"):
                    continue

                # дедуп по заголовку/ссылке
                key_t = title.lower()
                key_u = href.split("?")[0].rstrip("/")
                if key_t in seen_titles or key_u in seen_urls:
                    continue
                seen_titles.add(key_t)
                seen_urls.add(key_u)

                out.append({
                    "title": short(title, 120),
                    "summary": "",
                    "url": href,
                    "image": "",
                })

    random.Random(_daily_seed("ai")).shuffle(out)
    return out[:limit]

# ================== АФИША (KudaGo + Афиша.ру + Telegram, фильтры) ===================
async def get_afisha(limit: int = 10) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    # --- 1) KudaGo ---
    try:
        today = now_ts()
        month = int((datetime.now(MSK_TZ) + timedelta(days=30)).timestamp())
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            data = await fetch_json(
                client,
                "https://kudago.com/public-api/v1.4/events/",
                fields="title,dates,place,site_url,images,description",
                location="msk",
                actual_since=today,
                actual_until=month,
                page_size=40,
                order_by="-publication_date",
                expand="place",
                text_format="plain",
            )
            for e in (data or {}).get("results", []):
                title = (e.get("title") or "").strip()
                if not _is_allowed_event(title):
                    continue

                date_str = ""
                dates = e.get("dates") or []
                if dates and dates[0].get("start"):
                    try:
                        start = datetime.fromtimestamp(dates[0]["start"], MSK_TZ)
                        date_str = start.strftime("%d.%m %H:%M")
                    except Exception:
                        pass

                place = (e.get("place") or {}).get("title") or ""
                summary_parts = [date_str, place]
                summary = " · ".join(x for x in summary_parts if x) or (e.get("description") or "Событие")

                img = e["images"][0].get("image") if e.get("images") else ""
                items.append({
                    "title": short(title, 120),
                    "summary": short(summary, 240),
                    "url": e.get("site_url") or "",
                    "image": img,
                    "_src": "kudago",
                })
    except Exception:
        pass

    # --- 2) Afisha.ru ---
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            html_text = await fetch_html(client, "https://www.afisha.ru/msk/")
            if html_text:
                soup = BeautifulSoup(html_text, "html.parser")
                for a in soup.select("a[href]")[:120]:
                    text = a.get_text(" ", strip=True)
                    href = a.get("href") or ""
                    if not text or len(text) < 8:
                        continue
                    if href.startswith("/"):
                        href = "https://www.afisha.ru" + href
                    if not _is_allowed_event(text):
                        continue
                    items.append({
                        "title": short(text, 120),
                        "summary": "",
                        "url": href,
                        "image": "",
                        "_src": "afisha",
                    })
    except Exception:
        pass

    # --- 3) Telegram ---
    try:
        tg = await get_telegram_news(AFISHA_TELEGRAM, limit_per_channel=3, total_limit=12)
        for it in tg:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            if not _is_allowed_event(title):
                continue
            items.append({
                "title": short(title, 120),
                "summary": short(it.get("summary") or "", 240),
                "url": it.get("url") or "",
                "image": it.get("image") or "",
                "_src": "tg",
            })
    except Exception:
        pass

    # --- рандомизация на день и лимит ---
    random.Random(_daily_seed("afisha")).shuffle(items)
    return items[:limit]

def six_months_ago_str() -> str:
    return (datetime.now(MSK_TZ) - timedelta(days=182)).strftime("%Y-%m-%d")

def seed_for_today() -> int:
    """Один и тот же seed на текущую дату в MSK — порядок меняется раз в день."""
    return int(datetime.now(MSK_TZ).strftime("%Y%m%d"))

def shuffle_daily(items: list) -> list:
    """Детерминированная на день перестановка списка."""
    import random
    rnd = random.Random(seed_for_today())
    rnd.shuffle(items)
    return items

async def tmdb_collect(client: httpx.AsyncClient, url: str, base_params: dict, pages: int = 3) -> list[dict]:
    """Собираем несколько страниц TMDB для более широкого пула, чем одна страница."""
    out: list[dict] = []
    for p in range(1, pages + 1):
        params = dict(base_params)
        params["page"] = p
        data = await fetch_json(client, url, **params)
        results = (data or {}).get("results", []) or []
        if not results:
            break
        out.extend(results)
    return out

# ================== ОБНОВЛЕНО: СЕРИАЛЫ (рандомизация с дневной солью) ==================
async def get_series(limit: int = 5) -> List[Dict[str, Any]]:
    if not TMDB_API_KEY:
        return [{
            "title": "Нет TMDB ключа",
            "summary": "Добавьте TMDB_API_KEY в .env и перезапустите.",
            "url": "",
            "image": ""
        }]

    url = "https://api.themoviedb.org/3/discover/tv"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "ru-RU",
        "sort_by": "vote_average.desc",
        "vote_average.gte": 7.0,
        "first_air_date.gte": six_months_ago_str(),
        "vote_count.gte": 100,
        "include_adult": "false",
        "page": 1,
    }

    out: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        data = await fetch_json(client, url, **params)
        for tv in (data or {}).get("results", []):
            if tv.get("original_language") not in ("en", "ru", "ko", "ja", "es", "fr", "de", "it"):
                continue
            title = tv.get("name") or tv.get("original_name") or "Сериал"
            vote = tv.get("vote_average") or 0.0
            cnt = tv.get("vote_count") or 0
            overview = tv.get("overview") or "Описание отсутствует."
            poster = tv.get("poster_path") or ""
            img = f"https://image.tmdb.org/t/p/w780{poster}" if poster else ""
            tmdb_id = tv.get("id")
            more = f"https://www.themoviedb.org/tv/{tmdb_id}" if tmdb_id else ""
            rating_str = f"Рейтинг TMDB: {vote:.1f} ({cnt} оценок)" if vote > 0 else "Рейтинг TMDB: н/д"
            out.append({
                "title": title,
                "summary": f"{rating_str}. {short(overview, 220)}",
                "url": more,
                "image": img,
            })

    # === рандомизация на день ===
    today_salt = datetime.now(MSK_TZ).strftime("%Y-%m-%d") + "series"
    random.Random(today_salt).shuffle(out)

    return out[:limit]


# ================== ОБНОВЛЕНО: ФИЛЬМЫ (рандомизация с дневной солью) ===================
async def get_movies(limit: int = 5) -> List[Dict[str, Any]]:
    if not TMDB_API_KEY:
        return [{
            "title": "Нет TMDB ключа",
            "summary": "Добавьте TMDB_API_KEY в .env и перезапустите.",
            "url": "",
            "image": ""
        }]

    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "ru-RU",
        "sort_by": "vote_average.desc",
        "vote_average.gte": 7.0,
        "primary_release_date.gte": six_months_ago_str(),
        "vote_count.gte": 200,
        "include_adult": "false",
        "page": 1,
    }

    out: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        data = await fetch_json(client, url, **params)
        for mv in (data or {}).get("results", []):
            if mv.get("original_language") not in ("en", "ru", "ko", "ja", "es", "fr", "de", "it"):
                continue
            title = mv.get("title") or mv.get("original_title") or "Фильм"
            vote = mv.get("vote_average") or 0.0
            cnt = mv.get("vote_count") or 0
            overview = mv.get("overview") or "Описание отсутствует."
            poster = mv.get("poster_path") or ""
            img = f"https://image.tmdb.org/t/p/w780{poster}" if poster else ""
            tmdb_id = mv.get("id")
            more = f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id else ""
            rating_str = f"Рейтинг TMDB: {vote:.1f} ({cnt} оценок)" if vote > 0 else "Рейтинг TMDB: н/д"
            out.append({
                "title": title,
                "summary": f"{rating_str}. {short(overview, 220)}",
                "url": more,
                "image": img,
            })

    # === рандомизация на день ===
    today_salt = datetime.now(MSK_TZ).strftime("%Y-%m-%d") + "movies"
    random.Random(today_salt).shuffle(out)

    return out[:limit]

# ----------------------- HTML (UI) -----------------------
INDEX_HTML = f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{html.escape(APP_TITLE)}</title>

  <!-- PWA / базовые мета -->
  <meta name="theme-color" content="#0f1115">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Моя подборка">
  <link rel="manifest" href="/static/manifest.webmanifest">

  <!-- Иконки -->
  <link rel="apple-touch-icon" sizes="180x180" href="/static/icons/apple-touch-icon.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/static/icons/android-chrome-192x192.png">
  <link rel="icon" type="image/png" sizes="512x512" href="/static/icons/android-chrome-512x512.png">
  <link rel="icon" type="image/png" sizes="32x32" href="/static/icons/favicon-32x32.png">
  <link rel="icon" type="image/png" sizes="16x16" href="/static/icons/favicon-16x16.png">

  <style>
    :root {{
      --bg:#0f1115; --fg:#e9eef5; --muted:#97a1b3; --card:#171a21; --brand:#4ea3ff;
      --radius:18px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin:0; background:var(--bg); color:var(--fg);
      font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Inter,Arial;
      padding:24px 16px 40px;
    }}
    h1{{ margin:0 0 18px; font-size:36px; font-weight:800; }}

    .grid {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); }}
    .card {{
      background:var(--card); border-radius:var(--radius); overflow:hidden; cursor:pointer;
      border:1px solid #202533; transition:.2s transform;
    }}
    .card:hover{{ transform:translateY(-2px); }}
    .thumb{{ width:100%; height:150px; object-fit:cover; display:block; }}
    .title{{ padding:14px 16px 18px; font-size:22px; font-weight:800; }}
    #panel{{ margin-top:22px; }}
    .hidden{{ display:none; }}

    .item{{ background:var(--card); border:1px solid #202533; border-radius:var(--radius); overflow:hidden; margin:14px 0; }}
    .item .cover{{ width:100%; height:220px; object-fit:cover; display:block; }}
    .item .body{{ padding:14px 16px 16px; }}
    .item .name{{ font-size:22px; font-weight:800; margin:0 0 8px; }}
    .item .desc{{ color:var(--muted); margin:0 0 12px; }}
    .btn{{ display:inline-flex; align-items:center; gap:8px; background:#1f2937; color:#fff; text-decoration:none;
      border:1px solid #2a3446; padding:10px 14px; border-radius:11px; font-weight:600; }}

    /* === Кнопка установки PWA + подсказка === */
    .install-btn {{
      position: fixed; right: 16px; top: 16px; z-index: 1000;
      background:#1f2937; color:#fff; border:1px solid #2a3446;
      padding:10px 14px; border-radius:11px; font-weight:600;
      display:none; cursor:pointer;
    }}
    .install-hint {{
      position: fixed; right: 16px; top: 60px; z-index: 1000;
      background:#111827; color:#e5e7eb; border:1px solid #374151;
      padding:10px 12px; border-radius:10px; font-size:14px; display:none;
    }}
  </style>
</head>
<body>
  <h1>{datetime.now(MSK_TZ).strftime("%d %B %Y")}</h1>

  <!-- Кнопка установки и подсказка -->
  <button id="installBtn" class="install-btn">📲 Установить</button>
  <div id="installHint" class="install-hint">Нажмите «Установить», чтобы добавить на экран</div>

  <div class="grid">
    <div class="card" onclick="openTopic('afisha')">
      <img class="thumb" src="{TOPIC_IMAGES['afisha']}" alt="">
      <div class="title">Афиша Москвы</div>
    </div>
    <div class="card" onclick="openTopic('series')">
      <img class="thumb" src="{TOPIC_IMAGES['series']}" alt="">
      <div class="title">Сериалы (за 6 мес, ≥7.5)</div>
    </div>
    <div class="card" onclick="openTopic('movies')">
      <img class="thumb" src="{TOPIC_IMAGES['movies']}" alt="">
      <div class="title">Фильмы (за 6 мес, ≥7.5)</div>
    </div>
    <div class="card" onclick="openTopic('agro')">
      <img class="thumb" src="{TOPIC_IMAGES['agro']}" alt="">
      <div class="title">Агро-бизнес</div>
    </div>
    <div class="card" onclick="openTopic('svo')">
      <img class="thumb" src="{TOPIC_IMAGES['svo']}" alt="">
      <div class="title">Новости СВО</div>
    </div>
    <div class="card" onclick="openTopic('ai')">
      <img class="thumb" src="{TOPIC_IMAGES['ai']}" alt="">
      <div class="title">Новости ИИ</div>
    </div>
  </div>

  <div id="panel" class="hidden">
    <div id="output"></div>
  </div>

  <!-- Логика загрузки карточек -->
  <script>
    async function openTopic(key) {{
      const panel = document.getElementById('panel');
      const output = document.getElementById('output');
      panel.classList.remove('hidden');
      output.innerHTML = '<div class="item"><div class="body"><div class="name">Загрузка…</div><p class="desc">Получаю данные для: '+key+'</p></div></div>';

      try {{
        const r = await fetch('/data?topic=' + encodeURIComponent(key), {{
          headers: {{'ngrok-skip-browser-warning': 'true'}}
        }});
        const js = await r.json();
        if (!Array.isArray(js) || js.length === 0) {{
          output.innerHTML = '<div class="item"><div class="body"><div class="name">Пусто</div><p class="desc">Нет данных. Попробуйте позже.</p></div></div>';
          return;
        }}
        let html = '';
        js.forEach(function(it) {{
          html += '<div class="item">';
          if (it.image) html += '<img class="cover" src="'+it.image+'" alt="">';
          html += '<div class="body">';
          html += '<div class="name">'+(it.title || '')+'</div>';
          html += '<p class="desc">'+(it.summary || '')+'</p>';
          if (it.url) html += '<a class="btn" target="_blank" rel="noopener" href="'+it.url+'">Подробнее →</a>';
          html += '</div></div>';
        }});
        output.innerHTML = html;
      }} catch (e) {{
        output.innerHTML = '<div class="item"><div class="body"><div class="name">Ошибка</div><p class="desc">Не удалось загрузить.</p></div></div>';
      }}
      window.scrollTo({{top: panel.offsetTop - 8, behavior: 'smooth'}});
    }}
  </script>

  <!-- PWA: кнопка установки + SW -->
  <script>
    let deferredPrompt = null;
    const installBtn = document.getElementById('installBtn');
    const installHint = document.getElementById('installHint');

    // Показываем кнопку, когда браузер даёт право установки
    window.addEventListener('beforeinstallprompt', (e) => {{
      e.preventDefault();
      deferredPrompt = e;
      installBtn.style.display = 'block';
      installHint.style.display = 'block';
    }});

    // Нажатие на "Установить"
    installBtn.addEventListener('click', async () => {{
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const {{ outcome }} = await deferredPrompt.userChoice;
      // Прячем элементы после выбора
      installBtn.style.display = 'none';
      installHint.style.display = 'none';
      deferredPrompt = null;
      console.log(outcome === 'accepted' ? 'Установлено ✅' : 'Отменено ❌');
    }});

    // Регистрация Service Worker
    if ('serviceWorker' in navigator) {{
      window.addEventListener('load', () => {{
        navigator.serviceWorker.register('/static/sw.js')
          .then(reg => console.log('✅ Service Worker зарегистрирован:', reg))
          .catch(err => console.log('❌ Ошибка регистрации Service Worker:', err));
      }});
    }}
  </script>
</body>
</html>
"""

# ----------------------- ROUTES -----------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)

# ===== ОБНОВЛЕНО: добавлен параметр force=1 для обхода кэша =====
@app.get("/data", response_class=JSONResponse)
async def data(topic: str = Query(...), force: int = Query(0)) -> JSONResponse:
    topic = (topic or "").lower().strip()

    if not force:
        cached = cache_get(topic)
        if cached is not None:
            return JSONResponse(cached)

    items: List[Dict[str, Any]] = []
    try:
        if topic == "afisha":
            items = await get_afisha(limit=10)
        elif topic == "series":
            items = await get_series(limit=5)
        elif topic == "movies":
            items = await get_movies(limit=5)
        elif topic == "agro":
            items = await get_agro(limit=10)
        elif topic == "svo":
            items = await get_svo(limit=10)
        elif topic == "ai":
            items = await get_ai_news(limit=10)
        else:
            items = []
    except Exception:
        items = []

    cache_set(topic, items)
    return JSONResponse(items)