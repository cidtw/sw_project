#!/usr/bin/env python3
import json
import os
import requests
from datetime import datetime
import re

# 기존에 성공적으로 저장된 최종 대시보드 데이터 로드
# (실제 고도화 시에는 수집된 모든 공고가 담긴 DB나 json 폴더를 순회해야 합니다)
DATA_FILE = "data/final_recruit_dashboard.json"
REMIND_WEBHOOK_URL = "https://cloud.activepieces.com/api/v1/webhooks/418Pi7HTFbXYRh8nWfLVP"

def calculate_dday(deadline_str):
    # 정규식으로 '2026.07.05' 형태의 날짜 추출
    match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", deadline_str)
    if not match:
        return None
    
    target_date = datetime.strptime(f"{match.group(1)}-{match.group(2)}-{match.group(3)}", "%Y-%m-%d")
    today = datetime.now() # 시스템 현재 날짜 (2026년 기준 계산)
    
    # 시간 단위를 제외한 날짜 차이 계산
    delta = target_date.date() - today.date()
    return delta.days

def main():
    if not os.path.exists(DATA_FILE):
        print("조회할 채용 데이터가 없습니다.")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        jobs = json.load(f)
    
    # 만약 단일 객체라면 리스트로 래핑
    if not isinstance(jobs, list):
        jobs = [jobs]

    for job in jobs:
        dday = calculate_dday(job.get("deadline", ""))
        
        if dday in [3, 5, 7]: # D-3, D-5, D-7 타겟팅
            print(f"⏰ 마감 임박 공고 발견 (D-{dday}): {job['company']} - {job['title']}")
            
            # 리마인드용 페이로드 구성 (Activepieces로 전송)
            payload = job.copy()
            payload["dday"] = f"D-{dday}"
            payload["remind_title"] = f"⚠️ [마감 임박 리마인드] 서류 접수 종료까지 단 {dday}일!"
            
            # Activepieces 전송
            requests.post(REMIND_WEBHOOK_URL, json=payload)
        else:
            print(f"패스 (D-{dday}): {job['company']}")

if __name__ == "__main__":
    main()