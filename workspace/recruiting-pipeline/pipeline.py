#!/usr/bin/env python3
import json
import os
import re
import requests
import subprocess
import sys
import time

STATE_FILE = "data/pipeline_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "current_phase": "",
        "last_processed_id": "",
        "user_profile_hash": "default_hash",
        "last_run_timestamp": ""
    }

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def run_script(script_name):
    print(f"\n--- Running {script_name} ---")
    res = subprocess.run([sys.executable, script_name], capture_output=False)
    if res.returncode != 0:
        print(f"Error running {script_name}")
        sys.exit(res.returncode)

def dispatch_to_activepieces():
    # 검증을 통과한 최종 데이터 로드
    with open("./data/final_recruit_dashboard.json", "r", encoding="utf-8") as f:
        dashboard_data = json.load(f)
    
    # Activepieces Webhook URL
    activepieces_url = "https://cloud.activepieces.com/api/v1/webhooks/kYOBiWcUzz7gV1vzFob6l"
    
    # 데이터 전송
    headers = {"Content-Type": "application/json"}
    response = requests.post(activepieces_url, data=json.dumps(dashboard_data), headers=headers)
    
    if response.status_code == 200:
        print("Successfully dispatched to Activepieces Loop.")
    else:
        print(f"Failed to dispatch: {response.status_code}")

def main():
    state = load_state()
    start_phase = state.get("current_phase", "")
    print(f"Starting Recruiting Pipeline. Resuming from phase: {start_phase or 'START'}")
    
    if not start_phase or start_phase in ["START", "IDLE"]:
        state["current_phase"] = "FETCH"
        save_state(state)
        run_script("workspace/recruiting-pipeline/crawler.py")
        start_phase = "FETCH"
        
    if start_phase == "FETCH":
        state["current_phase"] = "ENRICH"
        save_state(state)
        run_script("workspace/recruiting-pipeline/enricher.py")
        start_phase = "ENRICH"
        
    if start_phase == "ENRICH":
        state["current_phase"] = "SCORE"
        save_state(state)
        run_script("workspace/recruiting-pipeline/scorer.py")
        start_phase = "SCORE"
        
    if start_phase == "SCORE":
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
                    
                # Rule 4-2: Data consistency check (ensure year matches)
                raw_item = next((r for r in raw_data if r["company"] == item["company"] and r["title"] == item["title"]), None)
                if raw_item:
                    raw_deadline = raw_item.get("deadline", "")
                    scored_deadline = item.get("deadline", "")
                    # Extract year numbers
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
                    # Self-Correction logic: re-run scorer
                    run_script("workspace/recruiting-pipeline/scorer.py")
                else:
                    print("Max retries exceeded. Transitioning to ERROR state.", file=sys.stderr)
                    state["current_phase"] = "ERROR"
                    save_state(state)
                    # Alert to Slack #system-error
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
                dispatch_to_activepieces()
            except Exception as e:
                print(f"Error dispatching to Activepieces: {e}", file=sys.stderr)
            
        state["current_phase"] = "IDLE"
        state["last_run_timestamp"] = time.strftime("%Y-%m-%d")
        if final_data:
            state["last_processed_id"] = final_data[0].get("company", "") + "_" + final_data[0].get("title", "")
        save_state(state)
        print("Pipeline run successfully terminated. State: IDLE. Sleep.")

if __name__ == "__main__":
    main()
