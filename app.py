from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import subprocess
import uuid
import threading
import os
import sys
import json
import time
import re
import platform
from pathlib import Path

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# When frozen by PyInstaller, __file__ lives inside the temp extraction dir.
# sys._MEIPASS is set by PyInstaller; fall back to the real script dir otherwise.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
    _BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent
    _BUNDLE_DIR = BASE_DIR

ENV_PATH = BASE_DIR / ".env"
SCRIPT_PATH = _BUNDLE_DIR / "sde_downloader.py"

# Current app version — keep in sync with GitHub release tags (e.g. "v1.2.0")
APP_VERSION = "v1.0.1"
GITHUB_REPO = "Luckci/sde-vault"

jobs = {}
jobs_lock = threading.Lock()


# ── .env helpers ──────────────────────────────────────────────────────────────

def read_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env(email, password, anthropic_key="", openai_key="", gemini_key=""):
    lines = [f"EMAIL={email}", f"PASSWORD={password}"]
    if anthropic_key:
        lines.append(f"ANTHROPIC_API_KEY={anthropic_key}")
    if openai_key:
        lines.append(f"OPENAI_API_KEY={openai_key}")
    if gemini_key:
        lines.append(f"GEMINI_API_KEY={gemini_key}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/env", methods=["GET"])
def get_env():
    env = read_env()
    email = env.get("EMAIL", "")
    return jsonify({
        "configured": bool(email and env.get("PASSWORD")),
        "email": email,
        "has_anthropic_key": bool(env.get("ANTHROPIC_API_KEY")),
        "has_openai_key":    bool(env.get("OPENAI_API_KEY")),
        "has_gemini_key":    bool(env.get("GEMINI_API_KEY")),
    })


@app.route("/api/env", methods=["POST"])
def save_env():
    data = request.get_json()
    email         = (data.get("email") or "").strip()
    password      = (data.get("password") or "").strip()
    anthropic_key = (data.get("anthropic_key") or "").strip()
    openai_key    = (data.get("openai_key") or "").strip()
    gemini_key    = (data.get("gemini_key") or "").strip()
    if not email or not password:
        return jsonify({"error": "Email og kodeord er påkrævet"}), 400
    write_env(email, password, anthropic_key, openai_key, gemini_key)
    return jsonify({"ok": True})


@app.route("/api/courses")
def list_courses():
    """Run --list in headless mode and return parsed course names."""
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--list", "--headless"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            cwd=str(BASE_DIR),
        )
        output = result.stdout + result.stderr
        courses = []
        in_list = False
        for line in output.splitlines():
            stripped = line.strip()
            if re.search(r"(tilgængelige|available)\s+kurser", stripped, re.IGNORECASE):
                in_list = True
                continue
            if not in_list:
                continue
            if not stripped or stripped.startswith("[") or stripped.startswith("=") or stripped.startswith("─"):
                continue
            # Strip numbering like "1. " or "- "
            course = re.sub(r"^[\d]+[.)]\s*", "", stripped)
            course = re.sub(r"^[-*]\s*", "", course)
            course = re.sub(r"\s*\(id=\d+\)\s*$", "", course)
            if course:
                courses.append(course)
        return jsonify({"courses": courses})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout – prøv igen"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/start", methods=["POST"])
def start_job():
    data = request.get_json()
    subject        = (data.get("subject") or "").strip()
    topic          = (data.get("topic") or "").strip()
    all_courses    = bool(data.get("all_courses"))
    ai_notes       = bool(data.get("ai_notes"))
    flashcards     = bool(data.get("flashcards"))
    headless       = bool(data.get("headless", True))
    resume         = bool(data.get("resume"))
    sync           = bool(data.get("sync"))
    quiz_notes     = bool(data.get("quiz_notes"))
    practice_exam  = bool(data.get("practice_exam"))
    concept_map    = bool(data.get("concept_map"))
    no_obsidian_config = bool(data.get("no_obsidian_config"))
    ai_provider    = (data.get("ai_provider") or "claude").strip()
    ai_model       = (data.get("ai_model") or "").strip()

    if not subject and not all_courses:
        return jsonify({"error": "Fag er påkrævet"}), 400

    if all_courses:
        cmd = [sys.executable, str(SCRIPT_PATH), "--all"]
    else:
        cmd = [sys.executable, str(SCRIPT_PATH), "--subject", subject]
    if topic:
        cmd += ["--topic", topic]
    if ai_notes:
        cmd.append("--ai-notes")
    if flashcards:
        cmd.append("--flashcards")
    if headless:
        cmd.append("--headless")
    if resume:
        cmd.append("--resume")
    if sync:
        cmd.append("--sync")
    if quiz_notes:
        cmd.append("--quiz-notes")
    if practice_exam:
        cmd.append("--practice-exam")
    if concept_map:
        cmd.append("--concept-map")
    if no_obsidian_config:
        cmd.append("--no-obsidian-config")
    if ai_notes or flashcards or practice_exam or concept_map:
        cmd += ["--ai-provider", ai_provider]
        if ai_model:
            cmd += ["--ai-model", ai_model]

    job_id = str(uuid.uuid4())

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(BASE_DIR),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    with jobs_lock:
        jobs[job_id] = {
            "process": process,
            "lines": [],
            "status": "running",
            "subject": subject,
            "topic": topic or None,
        }

    def reader():
        try:
            for line in process.stdout:
                with jobs_lock:
                    jobs[job_id]["lines"].append(line.rstrip("\n\r"))
            process.wait()
        finally:
            with jobs_lock:
                jobs[job_id]["status"] = "done" if process.returncode == 0 else "failed"

    threading.Thread(target=reader, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
def stream(job_id):
    with jobs_lock:
        if job_id not in jobs:
            return jsonify({"error": "Ikke fundet"}), 404

    def generate():
        sent = 0
        while True:
            with jobs_lock:
                job = jobs[job_id]
                lines = list(job["lines"])
                status = job["status"]

            while sent < len(lines):
                yield f"data: {json.dumps({'line': lines[sent]})}\n\n"
                sent += 1

            if status != "running":
                yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"
                break

            time.sleep(0.05)

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/stop/<job_id>", methods=["POST"])
def stop_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job:
        try:
            job["process"].terminate()
        except Exception:
            pass
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
    return jsonify({"ok": True})


@app.route("/api/open-vault")
def open_vault():
    vault_dir = BASE_DIR / "vault"
    vault_dir.mkdir(exist_ok=True)
    path = str(vault_dir)
    try:
        if platform.system() == "Windows":
            os.startfile(path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/version")
def version():
    """Return current version and check GitHub for a newer release."""
    info = {"current": APP_VERSION, "latest": None, "update_url": None}
    if not GITHUB_REPO:
        return jsonify(info)
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "SDE-Vault"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        latest = data.get("tag_name", "")
        info["latest"] = latest
        info["update_url"] = data.get("html_url", "")
        info["has_update"] = latest and latest != APP_VERSION
    except Exception:
        pass
    return jsonify(info)


if __name__ == "__main__":
    app.run(port=5000, debug=False, threaded=True)
