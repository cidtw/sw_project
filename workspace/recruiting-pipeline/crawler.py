#!/usr/bin/env python3
import urllib.request
import urllib.error
import re
import json
import sys
import sqlite3
import datetime
import html as html_lib
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from common import DB_PATH, FETCH_OUTPUT_PATH, normalized_job_key, write_json

BASE_URL = "https://www.jobkorea.co.kr"
STARTER_URL = "https://www.jobkorea.co.kr/starter/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"
}
KOREAN_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
UNKNOWN_DEADLINE = "마감일 확인 필요"
JOBKOREA_CHUNK_RE = re.compile(
    r'https?://job-hub-files[^"\'<>\s\\]+?_(?:OCR|DESCRIPTION)\.html[^"\'<>\s\\]*',
    re.IGNORECASE,
)
JOB_SECTION_HEADINGS = (
    "주요업무", "담당업무", "업무내용", "직무내용", "수행업무", "역할",
    "상세요강", "모집부문", "모집분야", "직무기술서",
    "Responsibilities", "Job Description", "Job Details",
    "자격요건", "지원자격", "필수요건", "필수사항", "응시자격", "기본요건", "지원요건",
    "Qualifications", "Required Qualifications", "Basic Qualifications",
    "우대사항", "우대조건", "우대요건", "우대자격", "Preferred Qualifications", "Preferences",
    "복리후생", "근무조건", "전형절차"
)

def get_normalized_key(company, title):
    return normalized_job_key(company, title)


def clean_listing_title(value):
    title = re.sub(r'\s+', ' ', str(value or '')).strip()
    title = re.sub(r'D[-_]?\d+\s*스크랩', '', title, flags=re.IGNORECASE)
    title = re.sub(r'D[-_]?\d+', '', title, flags=re.IGNORECASE)
    title = title.replace('스크랩', '')
    return title.strip()


def clean_detail_text(value):
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def compact_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def has_useful_job_text(value):
    text = compact_text(value)
    if len(text) < 80:
        return False
    return bool(re.search(r"(담당|업무|직무|자격|지원자격|필수|우대|요건|수행|근무)", text))


def merge_unique_text(parts):
    merged = []
    seen = set()
    for part in parts:
        for chunk in re.split(r"[\n\r]+", str(part or "")):
            chunk = compact_text(chunk)
            if len(chunk) < 8:
                continue
            key = re.sub(r"\W+", "", chunk).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(chunk)
    return "\n".join(merged)


def extract_heading_section(text, headings):
    source = compact_text(text)
    if not source:
        return ""
    boundary = r"(?![가-힣A-Za-z0-9])"
    heading_pattern = "|".join(r"\[?\s*" + re.escape(h) + r"\s*\]?" + boundary for h in headings)
    stop_pattern = "|".join(r"\[?\s*" + re.escape(h) + r"\s*\]?" + boundary for h in JOB_SECTION_HEADINGS if h not in headings)
    pattern = rf"(?:{heading_pattern})\s*[:：]?\s*(.*?)(?=(?:{stop_pattern})\s*[:：]?|$)"
    match = re.search(pattern, source, re.IGNORECASE)
    return compact_text(match.group(1)) if match else ""


def trim_text(text, limit=900):
    text = compact_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def extract_job_detail_fields(text):
    return {
        "responsibilities": trim_text(
            extract_heading_section(text, ["주요업무", "담당업무", "업무내용", "직무내용", "수행업무", "역할", "상세요강", "모집부문", "모집분야", "직무기술서", "Responsibilities", "Job Description", "Job Details"]),
            900,
        ),
        "requirements": trim_text(
            extract_heading_section(text, ["자격요건", "지원자격", "필수요건", "필수사항", "응시자격", "기본요건", "지원요건", "Qualifications", "Required Qualifications", "Basic Qualifications"]),
            900,
        ),
        "preferences": trim_text(
            extract_heading_section(text, ["우대사항", "우대조건", "우대요건", "우대자격", "Preferred Qualifications", "Preferences"]),
            900,
        ),
    }


def extract_text_from_markup(markup):
    soup = BeautifulSoup(markup or "", "html.parser")
    for node in soup.select("script, style, noscript"):
        node.decompose()
    return clean_detail_text(soup.get_text("\n", strip=True))


def extract_image_from_markup(markup, base_url):
    soup = BeautifulSoup(markup or "", "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            continue
        src_lower = src.lower()
        if not any(ext in src_lower for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
            continue
        if any(skip in src_lower for skip in ["logo", "icon", "button", "menu", "common", "loading", "share_default", "banner"]):
            continue
        return urljoin(base_url, src)
    return ""


def decode_jobkorea_flight_text(raw_html):
    text = html_lib.unescape(str(raw_html or ""))
    text = text.replace("\\u0026", "&").replace("\\/", "/").replace("\\u003d", "=")
    return text


def extract_jobkorea_chunk_urls(raw_html):
    decoded = decode_jobkorea_flight_text(raw_html)
    urls = []
    for match in JOBKOREA_CHUNK_RE.findall(decoded):
        url = match.rstrip('",')
        if url not in urls:
            urls.append(url)
    urls.sort(key=lambda value: (0 if "_DESCRIPTION.html" in value.upper() else 1, value))
    return urls


def fetch_jobkorea_description_chunks(raw_html, referer_url):
    chunks = []
    for chunk_url in extract_jobkorea_chunk_urls(raw_html):
        chunk_html = fetch_html(chunk_url, referer=referer_url, timeout=20)
        if not chunk_html:
            continue
        chunks.append({
            "url": chunk_url,
            "text": extract_text_from_markup(chunk_html),
            "image_url": extract_image_from_markup(chunk_html, chunk_url),
        })
    return chunks


def should_skip_embedded_url(value):
    lower = str(value or "").lower()
    if not lower:
        return True
    return any(token in lower for token in [
        "google", "doubleclick", "facebook", "analytics", "adservice", "adn.",
        "banner", "kakao", "naver.com/common", "youtube", "player", "map"
    ])


def collect_container_texts(soup, selectors):
    parts = []
    for selector in selectors:
        for node in soup.select(selector):
            text = extract_text_from_markup(str(node))
            if has_useful_job_text(text):
                parts.append(text)
    return parts


def fetch_embedded_documents(soup, base_url, referer_url, source_prefix):
    records = []
    for node in soup.select("iframe, frame, embed, object"):
        src = node.get("src") or node.get("data")
        if not src:
            continue
        embedded_url = urljoin(base_url, src)
        if should_skip_embedded_url(embedded_url):
            continue
        embedded_html = fetch_html(embedded_url, referer=referer_url, timeout=20)
        if not embedded_html:
            continue
        embedded_text = extract_text_from_markup(embedded_html)
        embedded_image = extract_image_from_markup(embedded_html, embedded_url)
        if has_useful_job_text(embedded_text) or embedded_image:
            records.append({
                "url": embedded_url,
                "text": embedded_text,
                "image_url": embedded_image,
                "method": f"{source_prefix}_embedded_document",
            })
    return records


def extract_platform_detail_sources(soup, raw_html, target_url, platform_name):
    selectors_by_platform = {
        "saramin": [
            "div.jv_detail", "div.jv_cont", "div.wrap_jv_cont", "section.jv_cont",
            "div.recruitment-summary", "div.user_content", "div.cont_recruit",
            "div.view_contents", "div.job_description", "div.recruit_view",
            "div.template_area", "div.job_view_content", "div.cont_box",
        ],
        "incruit": [
            "#jobpost", ".jobpost", ".jobpostContents", ".recru_info", ".recru-details",
            ".detailView", ".view_content", ".viewContents", ".job_view", ".job_info",
            ".recruit_view", ".tb_job", ".read", ".ifrm", ".cont", ".detail",
        ],
    }
    selectors = selectors_by_platform.get(platform_name, [])
    records = []
    container_text = merge_unique_text(collect_container_texts(soup, selectors))
    if container_text:
        records.append({
            "url": target_url,
            "text": container_text,
            "image_url": extract_image_from_markup(raw_html, target_url),
            "method": f"{platform_name}_detail_container",
        })

    records.extend(fetch_embedded_documents(soup, target_url, target_url, platform_name))
    if not records:
        text = extract_text_from_markup(raw_html)
        image_url = extract_image_from_markup(raw_html, target_url)
        if has_useful_job_text(text) or image_url:
            records.append({
                "url": target_url,
                "text": text,
                "image_url": image_url,
                "method": f"{platform_name}_html_scan",
            })
    return records


def render_with_playwright(url):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--lang=ko-KR"],
            )
            page = browser.new_page(
                user_agent=HEADERS["User-Agent"],
                locale="ko-KR",
                extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
            )
            page.goto(url, wait_until="networkidle", timeout=30000)
            try:
                page.wait_for_selector("body", timeout=5000)
            except Exception:
                pass
            content = page.content()
            browser.close()
            return content
    except Exception as exc:
        print(f"Playwright render fallback failed for {url}: {exc}", file=sys.stderr)
        return ""

def parse_clean_deadline(deadline_str):
    if not deadline_str:
        return UNKNOWN_DEADLINE

    deadline_str = re.sub(r'\s+', ' ', str(deadline_str)).strip()
    deadline_str = deadline_str.replace("스크랩", " ").strip()
    if not deadline_str:
        return UNKNOWN_DEADLINE

    if re.search(r'(상시|채용시|수시|접수중|open\s*until\s*filled)', deadline_str, re.IGNORECASE):
        if "상시" in deadline_str:
            return "상시채용"
        if "채용시" in deadline_str:
            return "채용시 마감"
        return "수시채용"

    # 1. Check for YYYY.MM.DD or YYYY-MM-DD pattern first
    match_yyyy_mm_dd = re.search(r'(\d{4})[./-](\d{2})[./-](\d{2})(?:\(([^)]+)\))?', deadline_str)
    if match_yyyy_mm_dd:
        year = match_yyyy_mm_dd.group(1)
        month = match_yyyy_mm_dd.group(2)
        day = match_yyyy_mm_dd.group(3)
        day_of_week = match_yyyy_mm_dd.group(4)
        if day_of_week:
            return f"~ {year}.{month}.{day}({day_of_week})"
        else:
            try:
                dt = datetime.datetime(int(year), int(month), int(day))
                day_of_week = KOREAN_WEEKDAYS[dt.weekday()]
                return f"~ {year}.{month}.{day}({day_of_week})"
            except Exception:
                return f"~ {year}.{month}.{day}"

    # 2. Check for MM/DD pattern (excluding YYYY prefix)
    match_mm_dd = re.search(r'(?<!\d)(\d{2})[./-](\d{2})(?:\(([^)]+)\))?', deadline_str)
    if match_mm_dd:
        month = match_mm_dd.group(1)
        day = match_mm_dd.group(2)
        day_of_week = match_mm_dd.group(3)

        now = datetime.datetime.now()
        year = now.year
        if int(month) < now.month - 1:
            year += 1

        if day_of_week:
            return f"~ {year}.{month}.{day}({day_of_week})"
        else:
            try:
                dt = datetime.datetime(year, int(month), int(day))
                day_of_week = KOREAN_WEEKDAYS[dt.weekday()]
                return f"~ {year}.{month}.{day}({day_of_week})"
            except Exception:
                return f"~ {year}.{month}.{day}"

    # 3. Check for D-day or D-N patterns, e.g. "D-5" or "D-10" or "D-day"
    match_dday = re.search(r'd[-_]?(day|\d+)', deadline_str, re.IGNORECASE)
    if match_dday:
        d_val = match_dday.group(1).lower()
        now = datetime.datetime.now()
        if d_val == 'day':
            days_to_add = 0
        else:
            days_to_add = int(d_val)
        target_date = now + datetime.timedelta(days=days_to_add)
        dow = KOREAN_WEEKDAYS[target_date.weekday()]
        return f"~ {target_date.year}.{target_date.strftime('%m')}.{target_date.strftime('%d')}({dow})"

    # 4. Check for "오늘" (today)
    if "오늘" in deadline_str or "금일" in deadline_str:
        now = datetime.datetime.now()
        dow = KOREAN_WEEKDAYS[now.weekday()]
        return f"~ {now.year}.{now.strftime('%m')}.{now.strftime('%d')}({dow})"

    # 5. Check for "내일" (tomorrow)
    if "내일" in deadline_str:
        tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
        dow = KOREAN_WEEKDAYS[tomorrow.weekday()]
        return f"~ {tomorrow.year}.{tomorrow.strftime('%m')}.{tomorrow.strftime('%d')}({dow})"

    # Listing cards sometimes expose the whole card text as the deadline.
    # Do not let company/title text leak into Slack as a bogus closing date.
    if len(deadline_str) > 20 or re.search(r'(모집|채용|개발|관리|담당|경력|신입|정규직|계약직)', deadline_str):
        return UNKNOWN_DEADLINE

    return deadline_str if re.search(r'(마감|D-|Dday|D-day)', deadline_str, re.IGNORECASE) else UNKNOWN_DEADLINE

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(jobs)")
    columns = [c[1] for c in cursor.fetchall()]
    if columns and "normalized_key" not in columns:
        print("Old schema detected (missing normalized_key). Dropping table jobs.")
        cursor.execute("DROP TABLE jobs")
    elif columns and "deep_scraped_json" not in columns:
        print("Old schema detected in jobs table. Dropping it.")
        cursor.execute("DROP TABLE jobs")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT,
            company TEXT,
            title TEXT,
            detail_url TEXT,
            deadline TEXT,
            image_url TEXT,
            deep_scraped_json TEXT,
            extracted_info_json TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_status INTEGER DEFAULT 0,
            normalized_key TEXT UNIQUE,
            UNIQUE(company, title)
        )
    """)
    conn.commit()
    conn.close()

def insert_jobs(jobs):
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    cursor = conn.cursor()
    new_count = 0
    for job in jobs:
        company = job.get("company", "")
        title = job.get("title", "")
        norm_key = get_normalized_key(company, title)
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO jobs (platform, company, title, detail_url, deadline, image_url, deep_scraped_json, extracted_info_json, normalized_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.get("platform", "JobKorea"),
                company,
                title,
                job.get("detail_url", ""),
                job.get("deadline", ""),
                job.get("image_url", ""),
                json.dumps(job.get("deep_scraped", {}), ensure_ascii=False),
                json.dumps(job.get("extracted_info", {}), ensure_ascii=False),
                norm_key
            ))
            if cursor.rowcount > 0:
                new_count += 1
            else:
                print(f"⏩ DB insert ignored (duplicate normalized_key): {norm_key}")
        except Exception as e:
            print(f"Error inserting job: {e}")
    conn.commit()
    conn.close()
    print(f"Inserted {new_count} new unique jobs into DB.")

def get_unsent_jobs():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT platform, company, title, detail_url, deadline, image_url, deep_scraped_json, extracted_info_json FROM jobs WHERE sent_status = 0")
    rows = cursor.fetchall()
    jobs = []
    for r in rows:
        jobs.append({
            "platform": r["platform"],
            "company": r["company"],
            "title": r["title"],
            "detail_url": r["detail_url"],
            "deadline": r["deadline"],
            "image_url": r["image_url"],
            "deep_scraped": json.loads(r["deep_scraped_json"]) if r["deep_scraped_json"] else {},
            "extracted_info": json.loads(r["extracted_info_json"]) if r["extracted_info_json"] else {}
        })
    conn.close()
    return jobs

def fetch_html(url, referer=None, timeout=15):
    headers = HEADERS.copy()
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            charset_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
            encodings = []
            if charset_match:
                encodings.append(charset_match.group(1))
            encodings.extend(["utf-8", "cp949", "euc-kr"])
            for encoding in encodings:
                try:
                    return raw.decode(encoding)
                except Exception:
                    continue
            return raw.decode("utf-8", "ignore")
    except Exception as e:
        print(f"Failed to fetch {url}: {e}", file=sys.stderr)
        return None

def parse_starter_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    listings = []

    items = soup.select('li.AgiCntnts')
    if not items:
        items = soup.select('ul.lst.starter-list li, ul.starter-list li, div.lstStarter li, div.filterList li, tr.dvResumeTr')
    if not items:
        items = soup.select('div.listList div.list-default, tr.dvResumeTr, li.starter-item, li.list-item')

    if not items:
        matches = re.findall(r'href="(/Recruit/GI_Read/\d+[^"]*)"[^>]*>([^<]+)</a>', html)
        for href, title in matches[:15]:
            listings.append({
                "company": "알수없음",
                "title": clean_listing_title(title),
                "detail_url": BASE_URL + href,
                "deadline": UNKNOWN_DEADLINE
            })
        return listings

    for item in items:
        try:
            co_el = item.select_one('strong.co, a.coLink, .coName, .corpName, .company')
            company = co_el.get_text(strip=True) if co_el else "알수없음"

            title_el = item.select_one('span.tx, a.link, a.AgiLink, .titLink, .title a')
            if not title_el:
                continue
            title = clean_listing_title(title_el.get_text(strip=True))

            link_el = item.select_one('a.AgiLink')
            href = ""
            if link_el:
                href = link_el.get('linkurl', '')
            if not href:
                href = title_el.get('href', '')

            if not href.startswith('http'):
                href = BASE_URL + href

            deadline_el = item.select_one('.day, .date, .time, .deadline')
            deadline = deadline_el.get_text(strip=True) if deadline_el else UNKNOWN_DEADLINE

            listings.append({
                "company": company,
                "title": title,
                "detail_url": href,
                "deadline": parse_clean_deadline(deadline)
            })
        except Exception:
            continue

    return listings

def deep_scrape_detail(url):
    target_url = url
    if "saramin.co.kr" in url:
        match = re.search(r'rec_idx=(\d+)', url)
        if match:
            rec_idx = match.group(1)
            target_url = f"https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={rec_idx}"
            print(f"Bypassing Saramin detail to AJAX: {target_url}")

    html = fetch_html(target_url)
    if not html:
        return {
            "employment_type": "정규직",
            "jd_summary": "상세 직무 기술 정보 추출 불가 (네트워크 오류/차단)",
            "welfare_tags": ["정보없음"],
            "scraped_image_url": ""
        }

    soup = BeautifulSoup(html, 'html.parser')
    detail_sources = []
    rendered_html = ""
    is_jobkorea = "jobkorea.co.kr" in target_url
    is_saramin = "saramin.co.kr" in target_url
    is_incruit = "incruit.com" in target_url
    if is_jobkorea:
        detail_sources = [
            {**chunk, "method": "jobkorea_description_chunk"}
            for chunk in fetch_jobkorea_description_chunks(html, target_url)
        ]
    elif is_saramin:
        detail_sources = extract_platform_detail_sources(soup, html, target_url, "saramin")
    elif is_incruit:
        detail_sources = extract_platform_detail_sources(soup, html, target_url, "incruit")

    # 1. Employment Type
    employment_type = "정규직"
    source_text = merge_unique_text([source.get("text", "") for source in detail_sources])
    text_content = merge_unique_text([soup.get_text("\n", strip=True), source_text])
    if (is_jobkorea or is_saramin or is_incruit) and not has_useful_job_text(text_content):
        rendered_html = render_with_playwright(url if is_saramin else target_url)
        if rendered_html:
            rendered_text = extract_text_from_markup(rendered_html)
            text_content = merge_unique_text([text_content, rendered_text])
            rendered_soup = BeautifulSoup(rendered_html, "html.parser")
            platform_name = "saramin" if is_saramin else ("incruit" if is_incruit else "jobkorea")
            detail_sources.extend(extract_platform_detail_sources(rendered_soup, rendered_html, url if is_saramin else target_url, platform_name))
            source_text = merge_unique_text([source.get("text", "") for source in detail_sources])
    if "계약직" in text_content:
        employment_type = "계약직"
    elif "인턴" in text_content:
        employment_type = "인턴"

    # 2. Welfare Tags
    welfare_tags = []
    for tag in soup.select('.welfare, .welfare-list, .welfare-tags span, .tag, div.jw_welfare_box span, div.welfare_info span'):
        welfare_tags.append(tag.get_text(strip=True))
    if not welfare_tags:
        keywords = ["주4.5일제", "자녀학자금", "주택자금대출", "유연근무", "도서구매비", "식대지원", "퇴직연금", "건강검진"]
        for kw in keywords:
            if kw in text_content:
                welfare_tags.append(kw)
    if not welfare_tags:
        welfare_tags = ["4대보험", "주5일제"]

    # 3. JD Summary
    jd_summary_parts = []
    jd_container = soup.select_one('.tbList, .artReadJobSum, .job-summary, .work-details, div.jv_detail, div.jv_cont, div.wrap_jv_cont, div.recru-details, div.recru_info, div.jobpostContents, div.job_description, div.content')
    if jd_container:
        jd_summary_parts.append(jd_container.get_text("\n", strip=True))
    else:
        paragraphs = [p.get_text(strip=True) for p in soup.select('p, div.text') if len(p.get_text(strip=True)) > 20]
        if paragraphs:
            jd_summary_parts.append("\n".join(paragraphs[:6]))
    if source_text:
        jd_summary_parts.append(source_text)
    if rendered_html:
        rendered_soup = BeautifulSoup(rendered_html, "html.parser")
        rendered_container = rendered_soup.select_one(
            '.tbList, .artReadJobSum, .job-summary, .work-details, div.jv_detail, div.jv_cont, div.wrap_jv_cont, div.recru-details, div.recru_info, div.jobpostContents, div.job_description, div.content'
        )
        if rendered_container:
            jd_summary_parts.append(rendered_container.get_text("\n", strip=True))

    jd_summary = merge_unique_text(jd_summary_parts)
    if not jd_summary or len(jd_summary) < 10:
        jd_summary = "공고 상세 직무 내용 확인 필요"
    structured_fields = extract_job_detail_fields(jd_summary)

    # 4. 이미지 주소 스캔
    scraped_image_url = ""
    for source in detail_sources:
        if source.get("image_url"):
            scraped_image_url = source["image_url"]
            break
    if not scraped_image_url:
        img_tags = soup.select('.gib_picture img, .template_area img, div.gi_gi_c img, div.jv_detail img, div.recru-details img, iframe')
        if not img_tags:
            img_tags = soup.find_all('img')

        for img in img_tags:
            src = img.get('src', '') or img.get('data-src', '')
            if not src:
                continue
            src_lower = src.lower()
            if any(x in src_lower for x in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                # Filter logos, icons, buttons, menus, and common sharing default templates
                if any(x in src_lower for x in ['logo', 'icon', 'button', 'menu', 'common', 'loading', 'share_default', 'banner']):
                    continue
                if src.startswith('//'):
                    scraped_image_url = "https:" + src
                elif src.startswith('/'):
                    parsed_url = urlparse(target_url)
                    scraped_image_url = f"{parsed_url.scheme}://{parsed_url.netloc}" + src
                else:
                    scraped_image_url = src
                break

    if not scraped_image_url:
        img_matches = re.findall(r'https?://[^\s"\'><]+?\.(?:jpg|jpeg|png|gif|webp)', html, re.IGNORECASE)
        if img_matches:
            for match in img_matches:
                match_lower = match.lower()
                if not any(x in match_lower for x in ['logo', 'icon', 'button', 'menu', 'common', 'loading', 'share_default', 'banner']):
                    scraped_image_url = match
                    break
    if not scraped_image_url and rendered_html:
        scraped_image_url = extract_image_from_markup(rendered_html, target_url)

    # 5. 공식 채용 공고 게시글 링크 파싱 시도 (포스코 및 일반 기업 대응)
    official_detail_url = ""
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        href_lower = href.lower()
        if any(x in href_lower for x in ['recruit', 'apply', 'career', 'h22a01-front', 'h22a1001']):
            if not any(p in href_lower for p in ['jobkorea', 'saramin', 'incruit', 'google', 'naver', 'daum', 'kakao', 'facebook', 'instagram', 'twitter', 'youtube', 'blog', 'tistory']):
                if href.startswith('http'):
                    official_detail_url = href
                    break

    return {
        "employment_type": employment_type,
        "jd_summary": jd_summary[:3000],
        "responsibilities": structured_fields.get("responsibilities", ""),
        "requirements": structured_fields.get("requirements", ""),
        "preferences": structured_fields.get("preferences", ""),
        "welfare_tags": list(set(welfare_tags))[:5],
        "scraped_image_url": scraped_image_url,
        "official_detail_url": official_detail_url,
        "description_source_urls": [source["url"] for source in detail_sources if source.get("url")],
        "detail_extraction_method": next(
            (source.get("method") for source in detail_sources if source.get("method")),
            "playwright_render" if rendered_html else "html",
        )
    }

def crawl_jobkorea():
    print("Crawling JobKorea...")
    html = fetch_html(STARTER_URL)
    if not html:
        print("JobKorea connection failure or blocked. Returning empty list.")
        return []

    listings = parse_starter_page(html)
    results = []
    for item in listings[:5]:
        print(f"Deep scraping detail page: {item['detail_url']}")
        detail_info = deep_scrape_detail(item['detail_url'])
        image_url = detail_info.get("scraped_image_url", "")

        results.append({
            "platform": "JobKorea",
            "company": item.get("company", "알수없음"),
            "title": item.get("title", ""),
            "detail_url": item.get("detail_url", ""),
            "deadline": parse_clean_deadline(item.get("deadline", UNKNOWN_DEADLINE)),
            "image_url": image_url,
            "deep_scraped": detail_info,
            "extracted_info": {
                "job_category": "IT / 데이터 / AI" if "AI" in item['title'] or "데이터" in item['title'] else "사무 / 기획",
                "career_level": "경력" if "경력" in item['title'] else "신입·경력",
                "education": "대졸↑",
                "location": ["서울"]
            }
        })
    return results

def crawl_saramin():
    print("Crawling Saramin...")
    url = "https://www.saramin.co.kr/zf_user/search/recruit?searchword=AI"
    html = fetch_html(url)
    if not html:
        print("Saramin connection failure or blocked. Returning empty list.")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    items = soup.select('.item_recruit')
    results = []
    for item in items[:5]:
        try:
            corp_el = item.select_one('.corp_name a, .corp_name')
            company = corp_el.get_text(strip=True) if corp_el else "알수없음"

            title_el = item.select_one('.job_tit a')
            if not title_el:
                continue
            href = title_el.get('href', '')
            if not href:
                continue
            title = clean_listing_title(title_el.get_text(strip=True))
            if not href.startswith('http'):
                href = "https://www.saramin.co.kr" + href

            deadline_el = item.select_one('.date')
            deadline = parse_clean_deadline(deadline_el.get_text(strip=True) if deadline_el else UNKNOWN_DEADLINE)

            print(f"Deep scraping Saramin detail: {href}")
            detail_info = deep_scrape_detail(href)
            image_url = detail_info.get("scraped_image_url", "")

            results.append({
                "platform": "Saramin",
                "company": company,
                "title": title,
                "detail_url": href,
                "deadline": deadline,
                "image_url": image_url,
                "deep_scraped": detail_info,
                "extracted_info": {
                    "job_category": "IT / 데이터 / AI" if "AI" in title or "데이터" in title else "사무 / 기획",
                    "career_level": "경력" if "경력" in title else "신입·경력",
                    "education": "대졸↑",
                    "location": ["서울"]
                }
            })
        except Exception:
            continue

    return results

def crawl_incruit():
    print("Crawling Incruit...")
    url = "https://search.incruit.com/list/search.asp?col=job&kw=AI"
    html = fetch_html(url)
    if not html:
        print("Incruit connection failure or blocked. Returning empty list.")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    items = soup.select('ul.c_row li')
    if not items:
        items = soup.select('div.cell_mid, div.n_job_list_default li, div.div_list_default tr, tr.dvResumeTr')
    results = []
    seen_detail_urls = set()
    for item in items:
        if len(results) >= 5:
            break
        try:
            corp_el = item.select_one('a[href*="incruit.com/company"], span.check_corp a, a.corp, a.corp_name')
            if not corp_el:
                corp_el = item.select_one('a[href*="corp"]')
            company = corp_el.get_text(strip=True) if corp_el else "알수없음"

            title_el = item.select_one('a[href*="jobdb_info/jobpost.asp"], span.check_subject a, a.title, a.jobLink')
            if not title_el:
                continue
            href = title_el.get('href', '')
            if not href:
                continue
            title = clean_listing_title(title_el.get_text(strip=True))
            if not href.startswith('http'):
                href = "https://job.incruit.com" + href
            if href in seen_detail_urls:
                continue
            seen_detail_urls.add(href)

            deadline_el = item.select_one('span.date, span.dday, .cell_last, .cl_btm')
            deadline_text = deadline_el.get_text(" ", strip=True) if deadline_el else item.get_text(" ", strip=True)
            deadline_match = re.search(r'(~\s*\d{1,2}[./-]\d{1,2}(?:\s*\([^)]+\))?|D[-_]?(?:day|\d+)|상시|채용시|오늘마감|내일마감)', deadline_text, re.IGNORECASE)
            deadline = parse_clean_deadline(deadline_match.group(1) if deadline_match else UNKNOWN_DEADLINE)

            print(f"Deep scraping Incruit detail: {href}")
            detail_info = deep_scrape_detail(href)
            image_url = detail_info.get("scraped_image_url", "")

            results.append({
                "platform": "Incruit",
                "company": company,
                "title": title,
                "detail_url": href,
                "deadline": deadline,
                "image_url": image_url,
                "deep_scraped": detail_info,
                "extracted_info": {
                    "job_category": "IT / 데이터 / AI" if "AI" in title or "데이터" in title else "사무 / 기획",
                    "career_level": "경력" if "경력" in title else "신입·경력",
                    "education": "대졸↑",
                    "location": ["서울"]
                }
            })
        except Exception:
            continue

    return results

def main():
    init_db()

    jk_jobs = crawl_jobkorea()
    saramin_jobs = crawl_saramin()
    incruit_jobs = crawl_incruit()

    all_jobs = jk_jobs + saramin_jobs + incruit_jobs
    insert_jobs(all_jobs)

    unsent_jobs = get_unsent_jobs()

    write_json(FETCH_OUTPUT_PATH, unsent_jobs)
    print(f"Saved {len(unsent_jobs)} unsent listings to {FETCH_OUTPUT_PATH}")

if __name__ == "__main__":
    main()
