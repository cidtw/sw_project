#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import requests
import threading
import shutil
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
SECRETS_FILE = BASE_DIR / "data" / "secrets.env"
PORT = int(os.environ.get("PORT", "8000"))
NGROK_DOMAIN = os.environ.get("NGROK_DOMAIN", "").strip()

def load_secrets():
    """Loads env variables from secrets.env file into os.environ if it exists."""
    if not SECRETS_FILE.exists():
        # Create directory if not exists
        SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Write template
        with open(SECRETS_FILE, "w", encoding="utf-8") as f:
            f.write("# Slack Bot configuration\n")
            f.write("SLACK_BOT_TOKEN=your-slack-bot-token-here\n")
            f.write("# Optional: OpenAI API configuration for enricher\n")
            f.write("OPENAI_API_KEY=your-openai-key-here\n")
        print(f"[*] Created credentials template file: {SECRETS_FILE}")
        print("[!] Please open it and paste your actual SLACK_BOT_TOKEN.")
        return False

    with open(SECRETS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Do not overwrite with placeholder if already defined in env
                existing_val = os.environ.get(key, "").strip()
                if not existing_val or "your-slack-bot-token-here" in existing_val or "your-openai-key-here" in existing_val:
                    os.environ[key] = val
    return True

def run():
    print("============================================================")
    print("[*] Slack Bot & ngrok FastAPI Server Initializer")
    print("============================================================")

    # 1. Load env vars
    load_secrets()

    # Check for SLACK_BOT_TOKEN
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not slack_token or "your-slack-bot-token-here" in slack_token:
        print("[WARNING] SLACK_BOT_TOKEN is not set or still has the placeholder value in data/secrets.env!")
        print("Please edit the secrets file and fill in your actual Slack Bot Token.")
        print(f"Path: {SECRETS_FILE}")
        sys.exit(1)

    print("[*] Env variables successfully configured.")

    # 2. Start FastAPI Server
    print(f"[*] Starting FastAPI Server on port {PORT}...")
    # Using sys.executable to run with the current python interpreter
    server_env = os.environ.copy()
    server_env["PORT"] = str(PORT)
    server_process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "slack_interactive_app.py"],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=server_env,
    )

    # Start a thread to read server output and print it to stdout
    def log_reader(proc):
        try:
            for line in proc.stdout:
                print(f"[FASTAPI] {line.strip()}")
        except Exception:
            pass
    
    log_thread = threading.Thread(target=log_reader, args=(server_process,), daemon=True)
    log_thread.start()

    # Wait a moment for FastAPI server to initialize
    time.sleep(2)

    # 3. Start ngrok (or check if already running)
    print("[*] Checking for existing ngrok tunnel...")
    tunnel_url = None
    existing_tunnel = False
    try:
        resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            for tunnel in data.get("tunnels", []):
                addr = tunnel.get("config", {}).get("addr", "")
                if f":{PORT}" in addr:
                    tunnel_url = tunnel.get("public_url", "")
                    existing_tunnel = True
                    print(f"[*] Reusing existing ngrok tunnel: {tunnel_url}")
                    break
    except Exception:
        pass

    ngrok_process = None
    if not existing_tunnel:
        if not shutil.which("ngrok"):
            print("[ERROR] ngrok executable was not found in PATH.")
            print("Install ngrok or start an existing tunnel before running this helper.")
            server_process.terminate()
            sys.exit(1)

        print(f"[*] Starting ngrok tunnel on port {PORT}...")
        ngrok_args = ["ngrok", "http", str(PORT)]
        if NGROK_DOMAIN:
            ngrok_args.append(f"--domain={NGROK_DOMAIN}")
        ngrok_process = subprocess.Popen(
            ngrok_args,
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait for ngrok to initialize and retrieve the tunnel URL
        print("[*] Waiting for ngrok tunnel to be established...")
        for attempt in range(10):
            time.sleep(1)
            try:
                resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
                if resp.status_code == 200:
                    data = resp.json()
                    tunnels = data.get("tunnels", [])
                    for tunnel in tunnels:
                        public_url = tunnel.get("public_url", "")
                        if public_url.startswith("https"):
                            tunnel_url = public_url
                            break
                    if not tunnel_url and tunnels:
                        tunnel_url = tunnels[0].get("public_url", "")
                    if tunnel_url:
                        break
            except Exception:
                pass

    if not tunnel_url:
        print("[ERROR] Failed to retrieve ngrok public URL!")
        print("Please check if ngrok is running and authenticated.")
        # Clean up
        server_process.terminate()
        if ngrok_process:
            ngrok_process.terminate()
        sys.exit(1)

    print("\n============================================================")
    print("Slack Bot & ngrok FastAPI Server Initialized Successfully!")
    print("============================================================")
    print(f"Local FastAPI URL:  http://localhost:{PORT}")
    print(f"ngrok Tunnel URL:   {tunnel_url}")
    print("\nConfigure your Slack App Settings with the following URLs:")
    print("------------------------------------------------------------")
    print("1. [Interactivity & Shortcuts] -> [Request URL]")
    print(f"   {tunnel_url}/slack/interactive")
    print("\n2. [Slash Commands] -> [/recruit] -> [Request URL]")
    print(f"   {tunnel_url}/slack/command")
    print("============================================================\n")
    print("Press Ctrl+C to terminate both servers.")

    try:
        while True:
            # Check if either process has died
            if server_process.poll() is not None:
                print(f"[ERROR] FastAPI server exited with code {server_process.returncode}")
                break
            if ngrok_process and ngrok_process.poll() is not None:
                print(f"[ERROR] ngrok exited with code {ngrok_process.returncode}")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Terminating servers...")
    finally:
        server_process.terminate()
        if ngrok_process:
            ngrok_process.terminate()
        # Wait a moment for processes to exit
        try:
            server_process.wait(timeout=3)
            if ngrok_process:
                ngrok_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # Force kill if needed
            server_process.kill()
            if ngrok_process:
                ngrok_process.kill()
        print("[*] Servers terminated cleanly.")

if __name__ == "__main__":
    run()
