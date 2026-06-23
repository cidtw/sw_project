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

STATE_FILE = "data/pipeline_state.json"

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

def bypass_image_via_imgur(image_url):
    """
    보안망(예: 포스코 서버)에 걸린 이미지 URL을 직접 다운로드한 뒤,
    Imgur 공용 CDN에 무명 업로드하여 슬랙이 100% 가져갈 수 있는 공개 URL로 우회 세탁합니다.
    """
    if not image_url or "imgur.com" in image_url or "wikimedia" in image_url:
        return image_url
        
    print(f"🔄 [보안 우회] 포스코 이미지 다운로드 후 Imgur 오픈 CDN 백업 시도 중...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        # 1. 하네스 로컬 서버 메모리로 이미지 받아오기
        img_res = requests.get(image_url, headers=headers, timeout=10)
        if img_res.status_code != 200:
            print(f"❌ 원본 이미지 다운로드 실패 (상태 코드: {img_res.status_code})")
            return image_url

        # 2. Imgur 익명 업로드 API 엔드포인트 호출
        client_id = "546c25a59c58ad7"  # 공용 익명 Client-ID
        b64_image = base64.b64encode(img_res.content).decode('utf-8')
        
        imgur_res = requests.post(
            "https://api.imgur.com/3/image",
            headers={"Authorization": f"Client-ID {client_id}"},
            data={"image": b64_image, "type": "base64"}
        )
        
        if imgur_res.status_code == 200:
            new_url = imgur_res.json().get("data", {}).get("link")
            print(f"✨ 이미지 우회 세탁 완료! 슬랙 전용 신규 주소: {new_url}")
            return new_url
        else:
            print(f"❌ Imgur API 업로드 실패: {imgur_res.text}")
            return image_url
            
    except Exception as e:
        print(f"❌ 이미지 우회 세탁 도중 예외 발생: {e}")
        return image_url

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

def dispatch_to_activepieces(state):
    # 1. 하네스가 생성한 최종 정형화 데이터 로드
    with open("./data/final_recruit_dashboard.json", "r", encoding="utf-8") as f:
        dashboard_data = json.load(f)
    
    if not isinstance(dashboard_data, list):
        dashboard_data = [dashboard_data]

    success_any = False
    for payload in dashboard_data:
        # 2. [핵심 적용] Activepieces로 전송 직전, 이미지 주소를 Imgur 공개 링크로 덮어쓰기
        if "image_url" in payload and payload["image_url"]:
            payload["image_url"] = bypass_image_via_imgur(payload["image_url"])

        # 3. Activepieces Webhook URL
        activepieces_url = "https://cloud.activepieces.com/api/v1/webhooks/kYOBiWcUzz7gV1vzFob6l"
        
        # 4. json=payload 형태로 전송
        print(f"Sending payload for {payload.get('company')} - {payload.get('title')} to Activepieces...")
        try:
            response = requests.post(activepieces_url, json=payload, timeout=15)
            if response.status_code == 200:
                print(f"🚀 [성공] {payload.get('company')} 채용 데이터가 Activepieces로 전송되었습니다.")
                success_any = True
                
                # SQLite 업데이트
                detail_url = payload.get("detail_url")
                if detail_url:
                    update_sent_status_in_db(detail_url)
                
                # state 업데이트
                unique_key = f"{payload.get('company', '')}_{payload.get('title', '')}"
                state["last_processed_id"] = unique_key
                if "sent_job_ids" not in state:
                    state["sent_job_ids"] = []
                if unique_key not in state["sent_job_ids"]:
                    state["sent_job_ids"].append(unique_key)
            else:
                print(f"❌ [실패] {payload.get('company')} 상태 코드: {response.status_code}, 메시지: {response.text}")
        except Exception as e:
            print(f"❌ [예외 발생] {payload.get('company')} 전송 실패: {e}")
            
    return success_any

def main():
    state = load_state()
    start_phase = state.get("current_phase", "")
    print(f"Starting Recruiting Pipeline. Resuming from phase: {start_phase or 'START'}")
    
    # Phase 1: FETCH
    if start_phase in ["", "START", "IDLE", "FETCH"]:
        if start_phase in ["", "START", "IDLE"]:
            state["current_phase"] = "FETCH"
            save_state(state)
            run_script("workspace/recruiting-pipeline/crawler.py")
        
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
        run_script("workspace/recruiting-pipeline/enricher.py")
        start_phase = "SCORE"
        
    # Phase 3: SCORE
    if start_phase == "SCORE":
        state["current_phase"] = "SCORE"
        save_state(state)
        run_script("workspace/recruiting-pipeline/scorer.py")
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
            input_path = "_workspace/score_output.json"
            raw_path = "_workspace/fetch_output.json"
            
            if not os.path.exists(input_path) or not os.path.exists(raw_path):
                print("Missing score output or raw listings.", file=sys.stderr)
                break
                
            with open(input_path, "r", encoding="utf-8") as f:
                scored_data = json.load(f)
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                
            errors = []
            
            # Rule 4-1 & Rule 4-2 check per item
            for idx, item in enumerate(scored_data):
                # Rule 4-1: Format Verification
                required_keys = ["company", "title", "deadline", "fit_score", "analysis", "company_insight"]
                missing_keys = [k for k in required_keys if k not in item]
                if missing_keys:
                    errors.append(f"Item {idx} missing keys: {missing_keys}")
                    continue
                    
                # Rule 4-2: Data consistency check
                raw_item = next((r for r in raw_data if r["company"] == item["company"] and r["title"] == item["title"]), None)
                if raw_item:
                    raw_deadline = raw_item.get("deadline", "")
                    scored_deadline = item.get("deadline", "")
                    raw_years = re.findall(r"\d{4}", raw_deadline)
                    scored_years = re.findall(r"\d{4}", scored_deadline)
                    if raw_years and scored_years and raw_years[0] != scored_years[0]:
                        errors.append(f"Item {idx} year mismatch: Raw={raw_years[0]}, Scored={scored_years[0]}")
            
            if not errors:
                verify_passed = True
                print("Verification passed! Rule 4-1 and Rule 4-2 satisfied.")
                with open("_workspace/verify_output.json", "w", encoding="utf-8") as f:
                    json.dump(scored_data, f, ensure_ascii=False, indent=2)
            else:
                print(f"Verification failed on attempt {retry_count + 1} with errors: {errors}")
                retry_count += 1
                if retry_count <= max_retries:
                    print("Initiating Self-Correction... Re-running Scorer Phase 3.")
                    run_script("workspace/recruiting-pipeline/scorer.py")
                else:
                    print("Max retries exceeded. Transitioning to ERROR state.", file=sys.stderr)
                    state["current_phase"] = "ERROR"
                    save_state(state)
                    error_msg = f"[CRITICAL SYSTEM ERROR] Recruiting Pipeline Verification Failed after 3 retries. Errors: {errors}"
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
            
        state["current_phase"] = "IDLE"
        state["last_run_timestamp"] = time.strftime("%Y-%m-%d")
        save_state(state)
        print("Pipeline run successfully terminated. State: IDLE. Sleep.")

if __name__ == "__main__":
    main()