"""
Resilient multi-stage Gemini model tier detection, profile management, and local sovereignty modes.
"""

import os
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from ..config import TIER_LEAN, TIER_STANDARD, TIER_ULTRA, TIER_LOCAL, TIER_OFFLINE, DB_PATH
from .db import get_connection, init_db


def check_local_endpoint(url: str = "http://localhost:11434/api/tags", timeout: float = 1.0) -> dict:
    """Checks availability of local inference engines (Ollama, vLLM, Codestral local)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SkillsDB/3.3"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return {"available": True, "models": models, "url": url}
    except Exception:
        pass
    return {"available": False, "models": [], "url": url}


def detect_model_tier(cid: str = None, db_path: Path = None) -> tuple[str, str]:
    """
    Detects the active model tier and returns (tier, source_description).
    Tiers:
      - 'ultra': Gemini Ultra, Gemini 2.0/3.x Pro (High/Ultra capability).
      - 'standard': Gemini Pro (Standard).
      - 'lean': Gemini Flash, Flash-Lite, or low-quota sessions.
      - 'local' / 'offline': Air-gapped local model (Codestral, Ollama, vLLM).
    """
    # Stage 1: Explicit environment variable override
    env_tier = (os.environ.get("SKILLSDB_MODEL_TIER") or os.environ.get("ANTIGRAVITY_MODEL", "")).strip().lower()
    if env_tier in [TIER_LEAN, TIER_STANDARD, TIER_ULTRA, TIER_LOCAL, TIER_OFFLINE]:
        return env_tier, f"Environment variable SKILLSDB_MODEL_TIER={env_tier}"
    if "local" in env_tier or "offline" in env_tier or "ollama" in env_tier or "codestral" in env_tier:
        return TIER_LOCAL, f"Environment variable ({env_tier}: Local sovereignty detected)"
    if "ultra" in env_tier:
        return TIER_ULTRA, f"Environment variable ({env_tier}: Ultra detected)"
    if "flash" in env_tier:
        return TIER_LEAN, f"Environment variable ({env_tier}: Flash detected)"

    # Stage 2: Database configuration override (runtime_config table)
    target_db = db_path or DB_PATH
    try:
        if target_db.exists():
            conn = get_connection(target_db)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM runtime_config WHERE key = 'model_tier';")
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                val = row[0].strip().lower()
                if val in [TIER_LEAN, TIER_STANDARD, TIER_ULTRA, TIER_LOCAL, TIER_OFFLINE]:
                    return val, f"Configured profile (runtime_config.model_tier={val})"
    except Exception:
        pass

    # Stage 3: Schema-resilient transcript inspection
    cid = cid or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    brain_dir = Path.home() / ".gemini" / "antigravity" / "brain"
    target_path = None
    if cid:
        candidate = brain_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if candidate.exists():
            target_path = candidate

    if not target_path and brain_dir.exists():
        try:
            transcripts = sorted(brain_dir.glob("*/.system_generated/logs/transcript.jsonl"), key=os.path.getmtime, reverse=True)
            if transcripts:
                target_path = transcripts[0]
        except Exception:
            pass

    if target_path and target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                head_lines = [f.readline() for _ in range(15)]
            for line in head_lines:
                if not line.strip():
                    continue
                lower_line = line.lower()
                if "gemini-ultra" in lower_line or "gemini-2.0-ultra" in lower_line or "gemini-3.0-ultra" in lower_line or "ultra" in lower_line:
                    return TIER_ULTRA, f"Transcript auto-detection ({target_path.parents[2].name}: Ultra detected)"
                if "gemini-flash" in lower_line or "flash" in lower_line or "flash-lite" in lower_line:
                    return TIER_LEAN, f"Transcript auto-detection ({target_path.parents[2].name}: Flash detected)"
                if "pro" in lower_line or "gemini-pro" in lower_line:
                    return TIER_STANDARD, f"Transcript auto-detection ({target_path.parents[2].name}: Pro detected)"
        except Exception:
            pass

    # Stage 4: Conservative default fallback
    return TIER_STANDARD, "Default baseline (Gemini Pro standard)"


def set_profile(tier_name: str, db_path: Path = None):
    """Explicitly sets or resets the persistent model profile in SQLite."""
    target_db = db_path or DB_PATH
    conn = get_connection(target_db)
    init_db(conn)
    cursor = conn.cursor()
    tier = tier_name.strip().lower()
    now_iso = datetime.now(timezone.utc).isoformat()

    if tier == "auto":
        cursor.execute("DELETE FROM runtime_config WHERE key = 'model_tier';")
        conn.commit()
        conn.close()
        current, src = detect_model_tier(db_path=target_db)
        print(f"[OK] Model profile reset to AUTO. Current detected tier: {current.upper()} ({src})")
    elif tier in [TIER_LEAN, TIER_STANDARD, TIER_ULTRA, TIER_LOCAL, TIER_OFFLINE]:
        cursor.execute("""
        INSERT OR REPLACE INTO runtime_config (key, value, updated_at)
        VALUES ('model_tier', ?, ?);
        """, (tier, now_iso))
        conn.commit()
        conn.close()
        print(f"[OK] Model profile explicitly set to: {tier.upper()}")
    else:
        conn.close()
        print(f"Error: Unknown profile '{tier_name}'. Choose from: ultra, standard, lean, local, offline, auto.")


def get_profile(cid: str = None):
    """Displays current active model profile, concurrency settings, and local endpoints."""
    tier, desc = detect_model_tier(cid)
    print("\n================ SKILLSDB ACTIVE RUNTIME PROFILE ================")
    print(f"Detected Tier:       {tier.upper()}")
    print(f"Detection Source:    {desc}")
    if tier == TIER_ULTRA:
        print("Concurrency Mode:    HIGH (Multi-threaded batching enabled)")
        print("Max Reader Workers:  16 parallel threads")
        print("Batch Retrieval:     Active (skillsdb get-skills / search-multi)")
        print("Skill Clusters:      Active (skillsdb get-cluster <domain>)")
    elif tier in [TIER_LOCAL, TIER_OFFLINE]:
        print("Concurrency Mode:    SOVEREIGN / LOCAL (Air-gapped offline mode)")
        print("Cloud Data Egress:   BLOCKED (Zero external network calls)")
        print("Inference Engine:    Local Codestral / Mistral / Ollama endpoint")
        local_status = check_local_endpoint()
        if local_status["available"]:
            models_str = ", ".join(local_status["models"]) if local_status["models"] else "Running (no tags)"
            print(f"Local Server:        Online at {local_status['url']} ({models_str})")
        else:
            print(f"Local Server:        Offline ({local_status['url']} unreachable)")
    elif tier == TIER_LEAN:
        print("Concurrency Mode:    LEAN (Token-conserving sequential mode)")
        print("Max Reader Workers:  1 thread")
        print("Micro-Skills:        Active (Use --section to preserve quota)")
    else:
        print("Concurrency Mode:    STANDARD (Balanced Pro mode)")
        print("Max Reader Workers:  4 parallel threads")
    print("=================================================================\n")
