#!/usr/bin/env python3
import json
import os
import re
import requests
import subprocess
import sys
import time
import base64
import sqlite3
import hashlib

STATE_FILE = "data/pipeline_state.json"

def calculate_file_hash(filepath):
    if not os.path.exists(filepath):
        return ""
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            buf = f.read()
            hasher.update(buf)
        return hasher.hexdigest()
    except Exception:
        return ""

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "current_phase": "",
        "last_processed_id": "",
        "user_profile_hash": "default_hash",
        "last_run_timestamp": "",
        "sent_job_ids": []  # 중복 방지용 고유 식별자 배열 추가
    }

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def run_script(script_name):
    print(f"\n--- Running {script_name} ---")
    res = subprocess.run([sys.executable, "-X", "utf8", script_name], capture_output=False)
    if res.returncode != 0:
        print(f"Error running {script_name}")
        sys.exit(res.returncode)

def update_sent_status_in_db(detail_url):
    db_path = "data/recruitment.db"
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE jobs SET sent_status = 1 WHERE detail_url = ?", (detail_url,))
        conn.commit()
        conn.close()
        print(f"Updated sent_status = 1 in SQLite for: {detail_url}")
    except Exception as e:
        print(f"Failed to update sent_status in DB: {e}", file=sys.stderr)

def preprocess_multi_source_payload(payload):
    """
    잡코리아, 사람인, 인크루트 데이터를 슬랙 블록킷 규격으로 통합 전처리합니다.
    Handlebars 문법 오류 방지 및 부실 텍스트 자동 폴백(Fallback)을 수행합니다.
    """
    # 1. Activepieces 자바스크립트 엔진 오류 방지를 위한 해시태그 배열 문자열 변환
    if "job_keywords" in payload and isinstance(payload["job_keywords"], list):
        payload["job_keywords_string"] = "   ".join(payload["job_keywords"])
    else:
        payload["job_keywords_string"] = "#채용 #직무역량 #취업준비"

    # 2. 직무기술서 부실 상태 방어 및 이미지 링크 마크다운 바인딩 (출력 포맷 통일)
    jd_text = payload.get("jd_summary", "").strip()
    img_url = payload.get("image_url", "").strip()
    
    # 만약 글자가 너무 짧거나 '참조' 문구만 있다면 긁어온 원본 이미지 마크다운으로 대치
    if not jd_text or "참조" in jd_text or len(jd_text) < 30:
        if img_url:
            payload["jd_summary"] = f"<{img_url}|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
        else:
            payload["jd_summary"] = "공고 상세 직무 내용을 참조하십시오."
    else:
        # 텍스트도 살아있고 이미지도 있다면 둘 다 볼 수 있게 하단에 하이퍼링크 추가
        if img_url:
            payload["jd_summary"] = f"{jd_text}\n\n👉 <{img_url}|🖼️ 채용 공고 원본 이미지 같이 보기>"

    return payload

def dispatch_to_activepieces(state):
    # 1. 하네스가 생성한 최종 정형화 데이터 로드
    with open("./data/final_recruit_dashboard.json", "r", encoding="utf-8") as f:
        dashboard_data = json.load(f)
    
    if isinstance(dashboard_data, list):
        if not dashboard_data:
            print("⚠ 발송할 채용 데이터가 비어있습니다.")
            return False
        payload = dashboard_data[0]
    else:
        payload = dashboard_data

    # 2. 잡코리아/사람인/인크루트 통합 전처리 로직 실행
    payload = preprocess_multi_source_payload(payload)

    # 3. Activepieces Webhook URL
    activepieces_url = "https://cloud.activepieces.com/api/v1/webhooks/kYOBiWcUzz7gV1vzFob6l"
    
    # 4. 전송
    response = requests.post(activepieces_url, json=payload)
    
    if response.status_code == 200:
        print("🚀 [성공] 보정된 채용 데이터가 Activepieces로 전송되었습니다.")
        
        # 5. 발송 성공 시 영구 중복 방지 캐시 메모리에 적재 및 SQLite 상태 동기화
        unique_key = f"{payload.get('company', '')}_{payload.get('title', '')}"
        if "sent_job_ids" not in state:
            state["sent_job_ids"] = []
        if unique_key not in state["sent_job_ids"]:
            state["sent_job_ids"].append(unique_key)
            
        state["last_processed_id"] = unique_key
        
        # DB 업데이트 호출
        if payload.get("detail_url"):
            update_sent_status_in_db(payload["detail_url"])
            
        return True
    else:
        print(f"❌ [실패] 상태 코드: {response.status_code}, 메시지: {response.text}")
        return False

def main():
    state = load_state()
    start_phase = state.get("current_phase", "")
    
    # Check user profile change
    profile_path = "data/user_profile.json"
    current_hash = calculate_file_hash(profile_path)
    old_hash = state.get("user_profile_hash", "")
    
    if current_hash and current_hash != old_hash:
        print(f"🔄 User profile change detected! Old hash: {old_hash}, New hash: {current_hash}")
        state["user_profile_hash"] = current_hash
        if start_phase in ["IDLE", "VERIFY", "DISPATCH", ""]:
            print("Resetting phase to SCORE to recalculate matching with new profile.")
            start_phase = "SCORE"
            state["current_phase"] = "SCORE"
        save_state(state)
        
    print(f"Starting Recruiting Pipeline. Resuming from phase: {start_phase or 'START'}")
    
    # Phase 1: FETCH
    if start_phase in ["", "START", "IDLE", "FETCH"]:
        if start_phase in ["", "START", "IDLE"]:
            state["current_phase"] = "FETCH"
            save_state(state)
            run_script("crawler.py")
        
        print("\n--- [Deduplication Control] 수집 데이터 중복 필터링 작동 ---")
        raw_path = "_workspace/fetch_output.json"
        
        if os.path.exists(raw_path):
            with open(raw_path, "r", encoding="utf-8") as f:
                fetched_jobs = json.load(f)
            
            sent_ids = state.get("sent_job_ids", [])
            filtered_jobs = []
            
            for job in fetched_jobs:
                unique_key = f"{job.get('company', '')}_{job.get('title', '')}"
                if unique_key in sent_ids:
                    print(f"⏩ 중복 송출 차단 (이미 발송된 공고): {unique_key}")
                else:
                    filtered_jobs.append(job)
            
            # 중복이 제거된 신규 공고 데이터로 수집 파일 갱신
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(filtered_jobs, f, ensure_ascii=False, indent=2)
            
            if not filtered_jobs:
                print("🛑 새롭게 처리할 신규 공고가 없습니다. 파이프라인을 조기 종료하고 대기 상태로 진입합니다.")
                state["current_phase"] = "IDLE"
                state["last_run_timestamp"] = time.strftime("%Y-%m-%d")
                save_state(state)
                sys.exit(0)
            else:
                print(f"✅ 필터링 완료: 총 {len(filtered_jobs)}개의 신규 공고 파이프라인 연산 진행.")
        
        start_phase = "ENRICH"
        
    # Phase 2: ENRICH
    if start_phase == "ENRICH":
        state["current_phase"] = "ENRICH"
        save_state(state)
        run_script("enricher.py")
        start_phase = "SCORE"
        
    # Phase 3: SCORE
    if start_phase == "SCORE":
        state["current_phase"] = "SCORE"
        save_state(state)
        run_script("scorer.py")
        start_phase = "VERIFY"
        
    # Phase 4: VERIFY
    if start_phase == "VERIFY":
        state["current_phase"] = "VERIFY"
        save_state(state)
        
        # Verification & Self-Correction Loop
        retry_count = 0
        max_retries = 3
        verify_passed = False
        
        while retry_count <= max_retries and not verify_passed:
            print(f"\n--- Verifying Scored Data (QA Checks) - Attempt {retry_count + 1} ---")
            
            # Run standalone verifier
            res = subprocess.run([sys.executable, "-X", "utf8", "verifier.py"], capture_output=False)
            
            if res.returncode == 0:
                verify_passed = True
                print("Verification passed! Rule 4-1 and Rule 4-2 satisfied.")
            else:
                print(f"Verification failed on attempt {retry_count + 1} with exit code: {res.returncode}")
                retry_count += 1
                if retry_count <= max_retries:
                    print("Initiating Self-Correction... Re-running Scorer Phase 3.")
                    run_script("scorer.py")
                else:
                    print("Max retries exceeded. Transitioning to ERROR state.", file=sys.stderr)
                    state["current_phase"] = "ERROR"
                    save_state(state)
                    error_msg = f"[CRITICAL SYSTEM ERROR] Recruiting Pipeline Verification Failed after 3 retries."
                    print(f"SLACK [#system-error]: {error_msg}", file=sys.stderr)
                    sys.exit(1)
                    
        start_phase = "VERIFY"
        
    if start_phase == "VERIFY" and state["current_phase"] != "ERROR":
        state["current_phase"] = "DISPATCH"
        save_state(state)
        
        print("\n--- Dispatching to Slack Block Kit (Webhook Trigger) ---")
        os.makedirs("data", exist_ok=True)
        final_data = []
        
        if os.path.exists("_workspace/verify_output.json"):
            with open("_workspace/verify_output.json", "r", encoding="utf-8") as f:
                final_data = json.load(f)
            with open("data/final_recruit_dashboard.json", "w", encoding="utf-8") as f:
                json.dump(final_data, f, ensure_ascii=False, indent=2)
            try:
                dispatch_to_activepieces(state)
            except Exception as e:
                print(f"Error dispatching to Activepieces: {e}", file=sys.stderr)
                
            # Run remind pipeline after successful dispatch
            print("\n--- Running Remind Pipeline ---")
            try:
                run_script("remind_pipeline.py")
            except Exception as e:
                print(f"Error running remind_pipeline.py: {e}", file=sys.stderr)
            
        state["current_phase"] = "IDLE"
        state["last_run_timestamp"] = time.strftime("%Y-%m-%d")
        save_state(state)
        print("Pipeline run successfully terminated. State: IDLE. Sleep.")

if __name__ == "__main__":
    main()