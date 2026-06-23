#!/usr/bin/env python3
import urllib.request
import urllib.error
import re
import json
import os
import sys
import sqlite3
import datetime
from bs4 import BeautifulSoup
from urllib.parse import urlparse

BASE_URL = "https://www.jobkorea.co.kr"
STARTER_URL = "https://www.jobkorea.co.kr/starter/"
DB_PATH = "data/recruitment.db"
DEFAULT_IMAGE = "https://images.unsplash.com/photo-1586281380349-632531db7ed4?w=500"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"
}

def get_normalized_key(company, title):
    # 회사명 정규화: (주), ㈜, 주식회사, (유), (유한), (재), 공백 제거
    norm_co = re.sub(r'[\s\(\)\[\]㈜재유한주식회사]', '', company)
    
    # 제목 정규화: 공백, 특수문자 제거, D-xx스크랩 제거
    norm_title = re.sub(r'[\s\(\)\[\]\-\_\,\.\!\?\&\@\:\;\|\'\"]', '', title)
    norm_title = re.sub(r'D-\d+스크랩', '', norm_title)
    
    # platform 접미사 제거
    norm_title = re.sub(r'채용$', '', norm_title)
    
    return f"{norm_co.lower()}_{norm_title.lower()}"

def parse_clean_deadline(deadline_str):
    if not deadline_str:
        return "~2026.07.05(일)"
    
    deadline_str = deadline_str.strip()
    
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
                weeks = ['월', '화', '수', '목', '금', '토', '일']
                day_of_week = weeks[dt.weekday()]
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
                weeks = ['월', '화', '수', '목', '금', '토', '일']
                day_of_week = weeks[dt.weekday()]
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
        weeks = ['월', '화', '수', '목', '금', '토', '일']
        dow = weeks[target_date.weekday()]
        return f"~ {target_date.year}.{target_date.strftime('%m')}.{target_date.strftime('%d')}({dow})"

    # 4. Check for "오늘" (today)
    if "오늘" in deadline_str:
        now = datetime.datetime.now()
        weeks = ['월', '화', '수', '목', '금', '토', '일']
        dow = weeks[now.weekday()]
        return f"~ {now.year}.{now.strftime('%m')}.{now.strftime('%d')}({dow})"
        
    # 5. Check for "내일" (tomorrow)
    if "내일" in deadline_str:
        tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
        weeks = ['월', '화', '수', '목', '금', '토', '일']
        dow = weeks[tomorrow.weekday()]
        return f"~ {tomorrow.year}.{tomorrow.strftime('%m')}.{tomorrow.strftime('%d')}({dow})"
        
    return deadline_str

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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

def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8', 'ignore')
    except Exception as e:
        print(f"Failed to fetch {url}: {e}", file=sys.stderr)
        return None

def parse_starter_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    listings = []
    
    items = soup.select('li.AgiCntnts')
    if not items:
        items = soup.select('ul.lst starter-list li, div.lstStarter li, div.filterList li, tr.dvResumeTr')
    if not items:
        items = soup.select('div.listList div.list-default, tr.dvResumeTr, li.starter-item, li.list-item')
        
    if not items:
        matches = re.findall(r'href="(/Recruit/GI_Read/\d+[^"]*)"[^>]*>([^<]+)</a>', html)
        for href, title in matches[:15]:
            listings.append({
                "company": "알수없음",
                "title": title.strip(),
                "detail_url": BASE_URL + href,
                "deadline": "~2026.07.05(일)"
            })
        return listings

    for item in items:
        try:
            co_el = item.select_one('strong.co, a.coLink, .coName, .corpName, .company')
            company = co_el.get_text(strip=True) if co_el else "알수없음"
            
            title_el = item.select_one('span.tx, a.link, a.AgiLink, .titLink, .title a')
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            
            link_el = item.select_one('a.AgiLink')
            href = ""
            if link_el:
                href = link_el.get('linkurl', '')
            if not href:
                href = title_el.get('href', '')
                
            if not href.startswith('http'):
                href = BASE_URL + href
                
            deadline_el = item.select_one('.day, .date, .time, .deadline')
            deadline = deadline_el.get_text(strip=True) if deadline_el else "~2026.07.05(일)"
            
            listings.append({
                "company": company,
                "title": title,
                "detail_url": href,
                "deadline": deadline
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
    
    # 1. Employment Type
    employment_type = "정규직"
    text_content = soup.get_text()
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
    jd_summary = ""
    jd_container = soup.select_one('.tbList, .artReadJobSum, .job-summary, .work-details, div.jv_detail, div.recru-details, div.job_description, div.content')
    if jd_container:
        jd_summary = jd_container.get_text(" ", strip=True)
    else:
        paragraphs = [p.get_text(strip=True) for p in soup.select('p, div.text') if len(p.get_text(strip=True)) > 20]
        if paragraphs:
            jd_summary = " ".join(paragraphs[:3])
            
    if not jd_summary or len(jd_summary) < 10:
        jd_summary = "공고 상세 직무 내용을 참조하십시오."
        
    # 4. 이미지 주소 스캔
    scraped_image_url = ""
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

    # 5. 공식 채용 공고 게시글 링크 파싱 시도 (포스코 및 일반 기업 대응)
    official_detail_url = ""
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        href_lower = href.lower()
        if any(x in href_lower for x in ['recruit', 'apply', 'career', 'h22a01-front', 'H22A1001']):
            if not any(p in href_lower for p in ['jobkorea', 'saramin', 'incruit', 'google', 'naver', 'daum', 'kakao', 'facebook', 'instagram', 'twitter', 'youtube', 'blog', 'tistory']):
                if href.startswith('http'):
                    official_detail_url = href
                    break

    return {
        "employment_type": employment_type,
        "jd_summary": jd_summary[:3000],
        "welfare_tags": list(set(welfare_tags))[:5],
        "scraped_image_url": scraped_image_url,
        "official_detail_url": official_detail_url
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
        image_url = detail_info.get("scraped_image_url")
        if not image_url:
            image_url = DEFAULT_IMAGE
            
        results.append({
            "platform": "JobKorea",
            "company": item.get("company", "알수없음"),
            "title": item.get("title", ""),
            "detail_url": item.get("detail_url", ""),
            "deadline": parse_clean_deadline(item.get("deadline", "~2026.07.05(일)")),
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
            title = title_el.get_text(strip=True)
            if not href.startswith('http'):
                href = "https://www.saramin.co.kr" + href
                
            deadline_el = item.select_one('.date')
            deadline = parse_clean_deadline(deadline_el.get_text(strip=True) if deadline_el else "~2026.07.05(일)")
            
            print(f"Deep scraping Saramin detail: {href}")
            detail_info = deep_scrape_detail(href)
            image_url = detail_info.get("scraped_image_url")
            if not image_url:
                image_url = DEFAULT_IMAGE
                
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
    items = soup.select('div.n_job_list_default li, div.div_list_default tr, tr.dvResumeTr')
    results = []
    for item in items[:5]:
        try:
            corp_el = item.select_one('span.check_corp a, a.corp, a.corp_name')
            if not corp_el:
                corp_el = item.select_one('a[href*="corp"]')
            company = corp_el.get_text(strip=True) if corp_el else "알수없음"
            
            title_el = item.select_one('span.check_subject a, a.title, a.jobLink')
            if not title_el:
                continue
            href = title_el.get('href', '')
            if not href:
                continue
            title = title_el.get_text(strip=True)
            if not href.startswith('http'):
                href = "https://job.incruit.com" + href
                
            deadline_el = item.select_one('span.date, span.dday')
            deadline = parse_clean_deadline(deadline_el.get_text(strip=True) if deadline_el else "~2026.07.05(일)")
            
            print(f"Deep scraping Incruit detail: {href}")
            detail_info = deep_scrape_detail(href)
            image_url = detail_info.get("scraped_image_url")
            if not image_url:
                image_url = DEFAULT_IMAGE
                
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
            
    os.makedirs("_workspace", exist_ok=True)
    with open("_workspace/fetch_output.json", "w", encoding="utf-8") as f:
        json.dump(unsent_jobs, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(unsent_jobs)} unsent listings to _workspace/fetch_output.json")

if __name__ == "__main__":
    main()