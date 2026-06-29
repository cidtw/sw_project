#!/usr/bin/env python3
import subprocess
import sys
import time
import sqlite3

from common import (
    BASE_DIR,
    DB_PATH,
    FETCH_OUTPUT_PATH,
    ENRICH_OUTPUT_PATH,
    SCORE_OUTPUT_PATH,
    FINAL_DASHBOARD_PATH,
    STATE_FILE,
    USER_PROFILE_PATH,
    VERIFY_OUTPUT_PATH,
    VERIFY_ERRORS_PATH,
    calculate_file_hash,
    normalized_job_key,
    read_json,
    write_json,
)

def load_state():
    return read_json(STATE_FILE, {
        "current_phase": "",
        "last_processed_id": "",
        "user_profile_hash": "default_hash",
        "last_run_timestamp": "",
        "sent_job_ids": []  # 중복 방지용 고유 식별자 배열 추가
    })

def save_state(state):
    write_json(STATE_FILE, state)

def run_script(script_name):
    print(f"\n--- Running {script_name} ---")
    script_path = BASE_DIR / script_name
    res = subprocess.run([sys.executable, "-X", "utf8", str(script_path)], cwd=BASE_DIR, capture_output=False)
    if res.returncode != 0:
        print(f"Error running {script_name}")
        sys.exit(res.returncode)

def update_sent_status_in_db(detail_url):
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute("UPDATE jobs SET sent_status = 1 WHERE detail_url = ?", (detail_url,))
        conn.commit()
        conn.close()
        print(f"Updated sent_status = 1 in SQLite for: {detail_url}")
    except Exception as e:
        print(f"Failed to update sent_status in DB: {e}", file=sys.stderr)

def finalize_processed_jobs(state):
    """검증된 공고를 처리 완료로 기록한다. Slack 송출은 FastAPI 앱이 직접 담당한다."""
    dashboard_data = read_json(FINAL_DASHBOARD_PATH, [])
    payloads = dashboard_data if isinstance(dashboard_data, list) else [dashboard_data]
    payloads = [payload for payload in payloads if isinstance(payload, dict)]

    if not payloads:
        print("⚠ 처리 완료로 기록할 채용 데이터가 비어있습니다.")
        return False

    for payload in payloads:
        unique_key = normalized_job_key(payload.get("company", ""), payload.get("title", ""))
        state.setdefault("sent_job_ids", [])
        if unique_key not in state["sent_job_ids"]:
            state["sent_job_ids"].append(unique_key)

        state["last_processed_id"] = unique_key

        if payload.get("detail_url"):
            update_sent_status_in_db(payload["detail_url"])

    print(f"Processed job summary: {len(payloads)} item(s) finalized for Slack direct serving.")
    return True


def main():
    state = load_state()
    start_phase = state.get("current_phase", "")
    if start_phase == "ERROR" or not start_phase:
        start_phase = "FETCH"
    
    # Check user profile change
    current_hash = calculate_file_hash(USER_PROFILE_PATH)
    old_hash = state.get("user_profile_hash", "")
    
    if current_hash and current_hash != old_hash:
        print(f"🔄 User profile change detected! Old hash: {old_hash}, New hash: {current_hash}")
        state["user_profile_hash"] = current_hash
        if start_phase in ["IDLE", "VERIFY", "DISPATCH", ""]:
            if ENRICH_OUTPUT_PATH.exists():
                print("Resetting phase to SCORE to recalculate matching with new profile.")
                start_phase = "SCORE"
                state["current_phase"] = "SCORE"
            else:
                print("Enrich output file not found. Starting from FETCH phase to gather data.")
                start_phase = "FETCH"
                state["current_phase"] = "FETCH"
        save_state(state)
        
    # Validate phase files availability before execution
    if start_phase == "ENRICH" and not FETCH_OUTPUT_PATH.exists():
        print("Fetch output not found. Falling back to FETCH.")
        start_phase = "FETCH"
    elif start_phase == "SCORE" and not ENRICH_OUTPUT_PATH.exists():
        print("Enrich output not found. Falling back to FETCH.")
        start_phase = "FETCH"
    elif start_phase == "VERIFY" and not SCORE_OUTPUT_PATH.exists():
        if ENRICH_OUTPUT_PATH.exists():
            print("Score output not found. Falling back to SCORE.")
            start_phase = "SCORE"
        else:
            print("Enrich output not found. Falling back to FETCH.")
            start_phase = "FETCH"
    elif start_phase == "DISPATCH" and not VERIFY_OUTPUT_PATH.exists():
        if SCORE_OUTPUT_PATH.exists():
            print("Verify output not found. Falling back to VERIFY.")
            start_phase = "VERIFY"
        else:
            print("Score output not found. Falling back to FETCH.")
            start_phase = "FETCH"

    print(f"Starting Recruiting Pipeline. Resuming from phase: {start_phase or 'START'}")
    
    # Phase 1: FETCH
    if start_phase in ["", "START", "IDLE", "FETCH"]:
        if start_phase in ["", "START", "IDLE"]:
            state["current_phase"] = "FETCH"
            save_state(state)
            run_script("crawler.py")
        
        print("\n--- [Deduplication Control] 수집 데이터 중복 필터링 작동 ---")
        if FETCH_OUTPUT_PATH.exists():
            fetched_jobs = read_json(FETCH_OUTPUT_PATH, [])
            
            sent_ids = state.get("sent_job_ids", [])
            filtered_jobs = []
            
            for job in fetched_jobs:
                unique_key = normalized_job_key(job.get("company", ""), job.get("title", ""))
                legacy_key = f"{job.get('company', '')}_{job.get('title', '')}"
                if unique_key in sent_ids or legacy_key in sent_ids:
                    print(f"⏩ 중복 송출 차단 (이미 발송된 공고): {unique_key}")
                else:
                    filtered_jobs.append(job)
            
            # 중복이 제거된 신규 공고 데이터로 수집 파일 갱신
            write_json(FETCH_OUTPUT_PATH, filtered_jobs)
            
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
        
        # Clean up any stale verify errors before validation starts
        if VERIFY_ERRORS_PATH.exists():
            VERIFY_ERRORS_PATH.unlink(missing_ok=True)
        
        # Verification & Self-Correction Loop
        retry_count = 0
        max_retries = 3
        verify_passed = False
        
        while retry_count <= max_retries and not verify_passed:
            print(f"\n--- Verifying Scored Data (QA Checks) - Attempt {retry_count + 1} ---")
            
            # Run standalone verifier
            verifier_path = BASE_DIR / "verifier.py"
            res = subprocess.run([sys.executable, "-X", "utf8", str(verifier_path)], cwd=str(BASE_DIR), capture_output=False)
            
            if res.returncode == 0:
                verify_passed = True
                if VERIFY_ERRORS_PATH.exists():
                    VERIFY_ERRORS_PATH.unlink(missing_ok=True)
                print("Verification passed! Rule 4-1 and Rule 4-2 satisfied.")
            else:
                print(f"Verification failed on attempt {retry_count + 1} with exit code: {res.returncode}")
                retry_count += 1
                if retry_count <= max_retries:
                    print("Initiating Self-Correction... Re-running Scorer Phase 3.")
                    run_script("scorer.py")
                else:
                    if VERIFY_ERRORS_PATH.exists():
                        VERIFY_ERRORS_PATH.unlink(missing_ok=True)
                    print("Max retries exceeded. Transitioning to ERROR state.", file=sys.stderr)
                    state["current_phase"] = "ERROR"
                    save_state(state)
                    error_msg = f"[CRITICAL SYSTEM ERROR] Recruiting Pipeline Verification Failed after 3 retries."
                    print(f"SLACK [#system-error]: {error_msg}", file=sys.stderr)
                    sys.exit(1)
                    
        start_phase = "DISPATCH"
        
    if start_phase == "DISPATCH" and state["current_phase"] != "ERROR":
        state["current_phase"] = "DISPATCH"
        save_state(state)
        
        print("\n--- Finalizing Slack Direct Serving Data ---")
        if VERIFY_OUTPUT_PATH.exists():
            final_data = read_json(VERIFY_OUTPUT_PATH, [])
            write_json(FINAL_DASHBOARD_PATH, final_data)
            try:
                finalize_processed_jobs(state)
            except Exception as e:
                print(f"Error finalizing processed jobs: {e}", file=sys.stderr)
            
        state["current_phase"] = "IDLE"
        state["last_run_timestamp"] = time.strftime("%Y-%m-%d")
        save_state(state)
        print("Pipeline run successfully terminated. State: IDLE. Sleep.")

if __name__ == "__main__":
    main()
