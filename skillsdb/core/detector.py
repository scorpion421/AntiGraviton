"""
Resilient multi-stage model, reasoning effort (Fast/Medium/High), quota pool, and account tier detection.
"""

import os
import re
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from ..config import (
    TIER_LEAN,
    TIER_LITE,
    TIER_STANDARD,
    TIER_PRO,
    TIER_ULTRA,
    TIER_LOCAL,
    TIER_OFFLINE,
    TIER_ALIASES,
    EFFORT_FAST,
    EFFORT_MEDIUM,
    EFFORT_HIGH,
    POOL_AGY_GEMINI_HIGH,
    POOL_AGY_GEMINI_FAST,
    POOL_AGY_CLAUDE,
    POOL_AGY_OPEN_WEIGHT,
    GEMINI_DIR,
    DB_PATH,
    __version__,
)
from .db import get_connection, init_db


ANTIGRAVITY_MODEL_CATALOG = {
    "gemini-4-argon": {
        "display_base": "Gemini 4 Argon",
        "pool": POOL_AGY_GEMINI_HIGH,
        "supported_efforts": [EFFORT_FAST, EFFORT_MEDIUM, EFFORT_HIGH],
        "default_effort": EFFORT_HIGH,
        "weights": {EFFORT_FAST: 1.2, EFFORT_MEDIUM: 2.0, EFFORT_HIGH: 3.5},
        "tiers": {EFFORT_FAST: TIER_STANDARD, EFFORT_MEDIUM: TIER_ULTRA, EFFORT_HIGH: TIER_ULTRA},
    },
    "gemini-3.8-flash": {
        "display_base": "Gemini 3.8 Flash",
        "pool": POOL_AGY_GEMINI_FAST,
        "supported_efforts": [EFFORT_FAST, EFFORT_MEDIUM, EFFORT_HIGH],
        "default_effort": EFFORT_MEDIUM,
        "weights": {EFFORT_FAST: 0.2, EFFORT_MEDIUM: 0.35, EFFORT_HIGH: 0.6},
        "tiers": {EFFORT_FAST: TIER_LEAN, EFFORT_MEDIUM: TIER_LEAN, EFFORT_HIGH: TIER_STANDARD},
    },
    "claude-opus-5.5": {
        "display_base": "Claude Opus 5.5",
        "pool": POOL_AGY_CLAUDE,
        "supported_efforts": [EFFORT_FAST, EFFORT_MEDIUM, EFFORT_HIGH],
        "default_effort": EFFORT_MEDIUM,
        "weights": {EFFORT_FAST: 1.5, EFFORT_MEDIUM: 2.5, EFFORT_HIGH: 4.0},
        "tiers": {EFFORT_FAST: TIER_STANDARD, EFFORT_MEDIUM: TIER_ULTRA, EFFORT_HIGH: TIER_ULTRA},
    },
    "claude-sonnet-5.5": {
        "display_base": "Claude Sonnet 5.5",
        "pool": POOL_AGY_CLAUDE,
        "supported_efforts": [EFFORT_FAST, EFFORT_MEDIUM, EFFORT_HIGH],
        "default_effort": EFFORT_MEDIUM,
        "weights": {EFFORT_FAST: 0.6, EFFORT_MEDIUM: 1.0, EFFORT_HIGH: 1.8},
        "tiers": {EFFORT_FAST: TIER_LEAN, EFFORT_MEDIUM: TIER_STANDARD, EFFORT_HIGH: TIER_STANDARD},
    },
    "gpt-oss-120b": {
        "display_base": "GPT-OSS 120B",
        "pool": POOL_AGY_OPEN_WEIGHT,
        "supported_efforts": [EFFORT_MEDIUM],
        "default_effort": EFFORT_MEDIUM,
        "weights": {EFFORT_MEDIUM: 0.25},
        "tiers": {EFFORT_MEDIUM: TIER_STANDARD},
    },
}


def tier_to_display_label(tier: str) -> str:
    """Maps internal tier constant ('ultra', 'standard', 'lean') to user-facing ('ULTRA', 'PRO', 'LITE')."""
    t = (tier or "").strip().lower()
    if t == TIER_ULTRA:
        return "ULTRA"
    if t in (TIER_STANDARD, "pro"):
        return "PRO"
    if t in (TIER_LEAN, "lite"):
        return "LITE"
    return t.upper() or "PRO"


def _has_explicit_effort_level(text: str) -> bool:
    """Returns True if the model selection string explicitly names a reasoning effort level (Fast/Medium/High)."""
    return bool(re.search(r"\b(?:high|medium|med|fast|low|lite)\b", (text or "").lower()))


def _extract_effort_level(text: str, supported: list[str], default_effort: str) -> str:
    """Extracts Fast, Medium, or High reasoning effort from a model selection string."""
    if len(supported) == 1:
        return supported[0]
    low = (text or "").lower()
    if re.search(r"\bhigh\b", low):
        return EFFORT_HIGH if EFFORT_HIGH in supported else default_effort
    if re.search(r"\b(?:medium|med)\b", low):
        return EFFORT_MEDIUM if EFFORT_MEDIUM in supported else default_effort
    if re.search(r"\b(?:fast|low|lite)\b", low):
        return EFFORT_FAST if EFFORT_FAST in supported else default_effort
    return default_effort


def parse_antigravity_model_spec(text: str, effort_override: str = None) -> dict | None:
    """
    Parses a model string into one of the 13 Antigravity model + effort specifications:
      - Gemini 4 Argon (Fast | Medium | High)
      - Gemini 3.8 Flash (Fast | Medium | High)
      - Claude Opus 5.5 (Fast | Medium | High)
      - Claude Sonnet 5.5 (Fast | Medium | High)
      - GPT-OSS 120B (fixed at Medium)
    """
    if not text:
        return None
    low = text.lower()
    family_key = None

    if any(k in low for k in ("argon", "gemini 4", "gemini-4")):
        family_key = "gemini-4-argon"
    elif any(k in low for k in ("3.8 flash", "3.8-flash", "gemini 3.8", "gemini-3.8", "gemini flash", "gemini-flash")) or re.search(r"\bgemini[- ]\d+(?:\.\d+)?[- ]flash\b", low):
        family_key = "gemini-3.8-flash"
    elif any(k in low for k in ("claude opus", "claude-opus", "opus 5.5", "opus-5.5", "opus 5")):
        family_key = "claude-opus-5.5"
    elif any(k in low for k in ("claude sonnet", "claude-sonnet", "sonnet 5.5", "sonnet-5.5", "sonnet 5")):
        family_key = "claude-sonnet-5.5"
    elif any(k in low for k in ("gpt-oss", "gpt oss", "120b")):
        family_key = "gpt-oss-120b"

    if not family_key:
        return None

    spec = ANTIGRAVITY_MODEL_CATALOG[family_key]
    supported = list(spec["supported_efforts"])
    if family_key == "gpt-oss-120b":
        effort = EFFORT_MEDIUM
    elif _has_explicit_effort_level(low):
        effort = _extract_effort_level(low, supported, spec["default_effort"])
    elif effort_override and effort_override.lower() in supported:
        effort = effort_override.lower()
    else:
        effort = spec["default_effort"]

    tier = spec["tiers"].get(effort, TIER_STANDARD)
    weight = spec["weights"].get(effort, 1.0)
    effort_cap = effort.capitalize()
    display_name = f"{spec['display_base']} ({effort_cap})"

    return {
        "model_family": family_key,
        "model_name": display_name,
        "pool": spec["pool"],
        "effort_level": effort,
        "supported_efforts": supported,
        "model_tier": tier,
        "tier_label": tier_to_display_label(tier),
        "quota_weight": weight,
    }


def check_local_endpoint(url: str = "http://localhost:11434/api/tags", timeout: float = 1.0) -> dict:
    """Checks availability of local inference engines (Ollama, vLLM, Codestral local)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"SkillsDB/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return {"available": True, "models": models, "url": url}
    except Exception:
        pass
    return {"available": False, "models": [], "url": url}


def _classify_model_string(text: str) -> str | None:
    """Classifies a model name or settings change string into a SkillsDB tier ('ultra', 'standard', 'lean')."""
    if not text:
        return None
    parsed = parse_antigravity_model_spec(text)
    if parsed:
        return parsed["model_tier"]

    low = text.lower().strip()
    if low in TIER_ALIASES:
        return TIER_ALIASES[low]
    if any(k in low for k in ("gemini-ultra", "gemini ultra", "gemini-2.0-ultra", "gemini-3.0-ultra", "pro (high)")):
        return TIER_ULTRA
    if any(k in low for k in ("flash-lite", "flash lite", "flash_lite", "flash (")):
        return TIER_LEAN
    if any(k in low for k in ("gemini-pro", "gemini pro", "pro (")):
        return TIER_STANDARD
    return None


def detect_account_tier(db_path: Path = None) -> tuple[str, str]:
    """
    Detects the subscription / account quota tier ('ultra' or 'pro').
    AntiGraviton is engineered strictly for professional coding and supports exclusively
    Google AI Ultra ('ultra') and Google AI Pro ('pro') subscription tiers.
    """
    env_acc = (os.environ.get("SKILLSDB_ACCOUNT_TIER") or "").strip().lower()
    if env_acc in ("ultra", "pro", "standard", "lite", "lean", "free"):
        norm = "ultra" if env_acc == "ultra" else "pro"
        return norm, f"Environment variable SKILLSDB_ACCOUNT_TIER={norm}"

    target_db = db_path or DB_PATH
    try:
        if target_db.exists():
            conn = get_connection(target_db)
            try:
                cur = conn.cursor()
                cur.execute("SELECT value FROM runtime_config WHERE key = 'account_tier';")
                row = cur.fetchone()
                if row and row[0]:
                    val = row[0].strip().lower()
                    if val in ("ultra", "pro", "standard", "lite", "lean", "free"):
                        norm = "ultra" if val == "ultra" else "pro"
                        return norm, f"Configured account profile (runtime_config.account_tier={norm})"
            finally:
                conn.close()
    except Exception:
        pass

    return "ultra", "Default subscription baseline (ULTRA)"


def detect_model_and_pool(cid: str = None, db_path: Path = None, model_name_hint: str = None) -> dict:
    """
    Detects the full 3D runtime state:
      1. Account subscription tier ('ultra', 'pro', 'lite')
      2. Active Antigravity model family & internal quota pool
      3. Active reasoning effort level ('fast', 'medium', 'high') and quota weight
    """
    target_db = db_path or DB_PATH
    account_tier, account_src = detect_account_tier(db_path=target_db)

    cfg_model_tier = ""
    cfg_effort = ""
    cfg_quota_mode = "auto"
    try:
        if target_db.exists():
            conn = get_connection(target_db)
            try:
                cur = conn.cursor()
                cur.execute("SELECT key, value FROM runtime_config WHERE key IN ('model_tier', 'effort_level', 'quota_mode');")
                rows = dict(cur.fetchall())
                cfg_model_tier = (rows.get("model_tier") or "").strip().lower()
                cfg_effort = (rows.get("effort_level") or "").strip().lower()
                cfg_quota_mode = (rows.get("quota_mode") or "auto").strip().lower()
            finally:
                conn.close()
    except Exception:
        pass

    if cfg_effort not in (EFFORT_FAST, EFFORT_MEDIUM, EFFORT_HIGH):
        cfg_effort = ""

    # Stage 1: Explicit environment variable or hook modelName hint
    clean_hint = (model_name_hint or "").strip()
    if clean_hint.lower() == "auto":
        clean_hint = ""
    env_hint = (clean_hint or os.environ.get("SKILLSDB_MODEL_TIER") or os.environ.get("ANTIGRAVITY_MODEL") or "").strip()
    if env_hint and env_hint.lower() != "auto":
        low_env = env_hint.lower()
        if any(k in low_env for k in ("local", "offline", "ollama", "codestral")):
            tier_val = TIER_OFFLINE if low_env == "offline" else TIER_LOCAL
            return {
                "model_tier": tier_val,
                "tier_label": tier_val.upper(),
                "account_tier": account_tier,
                "account_tier_source": account_src,
                "model_family": "local-endpoint",
                "model_name": "Local Sovereign Model",
                "pool": "local_sovereign",
                "effort_level": EFFORT_MEDIUM,
                "supported_efforts": [EFFORT_MEDIUM],
                "quota_weight": 0.0,
                "quota_mode": cfg_quota_mode,
                "source": f"Environment variable ({env_hint}: Local sovereignty detected)",
            }
        spec = parse_antigravity_model_spec(env_hint, effort_override=cfg_effort or None)
        if spec:
            label = "Argon/ULTRA" if spec["model_tier"] == TIER_ULTRA else ("Flash" if spec["model_tier"] == TIER_LEAN else "Pro")
            spec.update({
                "account_tier": account_tier,
                "account_tier_source": account_src,
                "quota_mode": cfg_quota_mode,
                "source": f"Model hint ({env_hint}: {label} detected)",
            })
            return spec
        if low_env in TIER_ALIASES:
            mapped_tier = TIER_ALIASES[low_env]
            default_family = "gemini-4-argon" if mapped_tier == TIER_ULTRA else ("gemini-3.8-flash" if mapped_tier == TIER_LEAN else "claude-sonnet-5.5")
            fallback_spec = parse_antigravity_model_spec(default_family, effort_override=cfg_effort or None)
            fallback_spec["model_tier"] = mapped_tier
            fallback_spec["tier_label"] = tier_to_display_label(mapped_tier)
            fallback_spec.update({
                "account_tier": account_tier,
                "account_tier_source": account_src,
                "quota_mode": cfg_quota_mode,
                "source": f"Environment variable SKILLSDB_MODEL_TIER={mapped_tier}",
            })
            return fallback_spec
        classified = _classify_model_string(low_env)
        if classified:
            label = "Argon/ULTRA" if classified == TIER_ULTRA else ("Flash" if classified == TIER_LEAN else "Pro")
            default_family = "gemini-4-argon" if classified == TIER_ULTRA else ("gemini-3.8-flash" if classified == TIER_LEAN else "claude-sonnet-5.5")
            fallback_spec = parse_antigravity_model_spec(default_family, effort_override=cfg_effort or None)
            fallback_spec["model_tier"] = classified
            fallback_spec["tier_label"] = tier_to_display_label(classified)
            fallback_spec.update({
                "account_tier": account_tier,
                "account_tier_source": account_src,
                "quota_mode": cfg_quota_mode,
                "source": f"Model hint ({env_hint}: {label} detected)",
            })
            return fallback_spec

    # Stage 2: Database configuration override (runtime_config.model_tier)
    if cfg_model_tier and cfg_model_tier != "auto":
        if cfg_model_tier in TIER_ALIASES:
            mapped_tier = TIER_ALIASES[cfg_model_tier]
            if mapped_tier in (TIER_LOCAL, TIER_OFFLINE):
                return {
                    "model_tier": mapped_tier,
                    "tier_label": mapped_tier.upper(),
                    "account_tier": account_tier,
                    "account_tier_source": account_src,
                    "model_family": "local-endpoint",
                    "model_name": "Local Sovereign Model",
                    "pool": "local_sovereign",
                    "effort_level": EFFORT_MEDIUM,
                    "supported_efforts": [EFFORT_MEDIUM],
                    "quota_weight": 0.0,
                    "quota_mode": cfg_quota_mode,
                    "source": f"Configured profile (runtime_config.model_tier={mapped_tier})",
                }
            default_family = "gemini-4-argon" if mapped_tier == TIER_ULTRA else ("gemini-3.8-flash" if mapped_tier == TIER_LEAN else "claude-sonnet-5.5")
            spec = parse_antigravity_model_spec(default_family, effort_override=cfg_effort or None)
            spec["model_tier"] = mapped_tier
            spec["tier_label"] = tier_to_display_label(mapped_tier)
            spec.update({
                "account_tier": account_tier,
                "account_tier_source": account_src,
                "quota_mode": cfg_quota_mode,
                "source": f"Configured profile (runtime_config.model_tier={mapped_tier})",
            })
            return spec

    # Stage 3: Schema-resilient transcript inspection (uses GEMINI_DIR via resolve_user_home())
    cid = cid or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    brain_dir = GEMINI_DIR / "antigravity" / "brain"
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
            conv_name = target_path.parents[2].name
            file_size = target_path.stat().st_size
            read_bytes = min(file_size, 4194304)
            with open(target_path, "rb") as f:
                if file_size > read_bytes:
                    f.seek(file_size - read_bytes)
                tail_text = f.read().decode("utf-8", errors="ignore")

            settings_matches = re.findall(
                r"<USER_SETTINGS_CHANGE>(?:\\n|\s)*The user changed (?:the )?setting (?:\\[\"'`]|[\"'`])?Model Selection(?:\\[\"'`]|[\"'`])? from [^<]+? to ([^\n<\\]+?)\.(?=\s|\\n|<|\"|$)",
                tail_text,
            )
            for target_model in reversed(settings_matches):
                spec = parse_antigravity_model_spec(target_model, effort_override=cfg_effort or None)
                if spec:
                    label = f"{target_model.strip()} -> ULTRA" if spec["model_tier"] == TIER_ULTRA else ("Flash" if spec["model_tier"] == TIER_LEAN else "Pro")
                    spec.update({
                        "account_tier": account_tier,
                        "account_tier_source": account_src,
                        "quota_mode": cfg_quota_mode,
                        "source": f"Transcript auto-detection ({conv_name}: {label} detected)",
                    })
                    return spec
                tier = _classify_model_string(target_model)
                if tier:
                    label = f"{target_model.strip()} -> ULTRA" if tier == TIER_ULTRA else ("Flash" if tier == TIER_LEAN else "Pro")
                    default_family = "gemini-4-argon" if tier == TIER_ULTRA else ("gemini-3.8-flash" if tier == TIER_LEAN else "claude-sonnet-5.5")
                    fallback_spec = parse_antigravity_model_spec(default_family, effort_override=cfg_effort or None)
                    fallback_spec["model_tier"] = tier
                    fallback_spec["tier_label"] = tier_to_display_label(tier)
                    fallback_spec.update({
                        "account_tier": account_tier,
                        "account_tier_source": account_src,
                        "quota_mode": cfg_quota_mode,
                        "source": f"Transcript auto-detection ({conv_name}: {label} detected)",
                    })
                    return fallback_spec

            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                head_lines = [f.readline() for _ in range(15)]
            for line in head_lines:
                if not line.strip() or "invoke_subagent" in line or "flash_lite" in line:
                    continue
                spec = parse_antigravity_model_spec(line, effort_override=cfg_effort or None)
                if spec:
                    label = "Argon/Ultra" if spec["model_tier"] == TIER_ULTRA else ("Flash" if spec["model_tier"] == TIER_LEAN else "Pro")
                    spec.update({
                        "account_tier": account_tier,
                        "account_tier_source": account_src,
                        "quota_mode": cfg_quota_mode,
                        "source": f"Transcript auto-detection ({conv_name}: {label} detected)",
                    })
                    return spec
        except Exception:
            pass

    # Stage 4: Default baseline fallback (Gemini 4 Argon High on Ultra account, or Pro baseline)
    base_spec = parse_antigravity_model_spec("Gemini 4 Argon", effort_override=cfg_effort or EFFORT_HIGH)
    if account_tier == "ultra":
        base_spec.update({
            "account_tier": account_tier,
            "account_tier_source": account_src,
            "quota_mode": cfg_quota_mode,
            "source": "Default baseline (Ultra / Gemini 4 Argon)",
        })
        return base_spec

    base_spec["model_tier"] = TIER_STANDARD
    base_spec["tier_label"] = "PRO"
    base_spec.update({
        "account_tier": account_tier,
        "account_tier_source": account_src,
        "quota_mode": cfg_quota_mode,
        "source": "Default baseline (Standard / Pro mode)",
    })
    return base_spec


def detect_model_tier(cid: str = None, db_path: Path = None, model_name_hint: str = None) -> tuple[str, str]:
    """
    Detects the active model tier and returns (tier, source_description).
    Maintains 100% backward compatibility with all existing callers.
    """
    info = detect_model_and_pool(cid=cid, db_path=db_path, model_name_hint=model_name_hint)
    return info["model_tier"], info["source"]


def set_profile(
    tier_name: str = None,
    db_path: Path = None,
    account_tier: str = None,
    effort_level: str = None,
    quota_mode: str = None,
    api_key: str = None,
):
    """Explicitly sets or resets persistent model, account quota tier, effort level, and routing mode in SQLite."""
    target_db = db_path or DB_PATH
    conn = get_connection(target_db)
    now_iso = datetime.now(timezone.utc).isoformat()
    updates_made = []
    try:
        init_db(conn)
        cursor = conn.cursor()

        if tier_name:
            raw_t = tier_name.strip().lower()
            if raw_t == "auto":
                cursor.execute("DELETE FROM runtime_config WHERE key = 'model_tier';")
                updates_made.append("model_tier=AUTO")
            elif raw_t in TIER_ALIASES:
                norm_t = TIER_ALIASES[raw_t]
                cursor.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('model_tier', ?, ?);",
                    (norm_t, now_iso),
                )
                updates_made.append(f"model_tier={norm_t.upper()} ({tier_to_display_label(norm_t)})")
            else:
                print(f"Error: Unknown profile '{tier_name}'. Choose from: ultra, pro, lite, standard, lean, local, offline, auto.")
                return

        if account_tier:
            acc_clean = account_tier.strip().lower()
            if acc_clean == "auto":
                cursor.execute("DELETE FROM runtime_config WHERE key = 'account_tier';")
                updates_made.append("account_tier=AUTO")
            elif acc_clean in ("ultra", "pro", "standard", "lite", "lean", "free"):
                norm_acc = "ultra" if acc_clean == "ultra" else "pro"
                cursor.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('account_tier', ?, ?);",
                    (norm_acc, now_iso),
                )
                updates_made.append(f"account_tier={norm_acc.upper()}")
            else:
                print(f"Error: Unknown account tier '{account_tier}'. Supported coding subscription tiers: ultra, pro, auto.")
                return

        if effort_level:
            eff_clean = effort_level.strip().lower()
            if eff_clean == "auto":
                cursor.execute("DELETE FROM runtime_config WHERE key = 'effort_level';")
                updates_made.append("effort_level=AUTO")
            elif eff_clean in (EFFORT_FAST, EFFORT_MEDIUM, EFFORT_HIGH):
                cursor.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('effort_level', ?, ?);",
                    (eff_clean, now_iso),
                )
                updates_made.append(f"effort_level={eff_clean.upper()}")
            else:
                print(f"Error: Unknown effort level '{effort_level}'. Choose from: fast, medium, high, auto.")
                return

        if quota_mode:
            mode_clean = quota_mode.strip().lower()
            if mode_clean in ("auto", "balanced", "golden_ratio", "shield", "conserve", "turbo", "direct"):
                norm_mode = "shield" if mode_clean == "conserve" else mode_clean
                cursor.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('quota_mode', ?, ?);",
                    (norm_mode, now_iso),
                )
                updates_made.append(f"quota_mode={norm_mode.upper()}")
            else:
                print(f"Error: Unknown quota mode '{quota_mode}'. Choose from: auto, balanced, golden_ratio, shield, turbo, direct.")
                return

        if api_key is not None:
            key_clean = api_key.strip()
            if not key_clean or key_clean.lower() in ("clear", "none", "delete"):
                cursor.execute("DELETE FROM runtime_config WHERE key = 'gemini_api_key';")
                updates_made.append("gemini_api_key=CLEARED")
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('gemini_api_key', ?, ?);",
                    (key_clean, now_iso),
                )
                updates_made.append("gemini_api_key=CONFIGURED")

        conn.commit()
    finally:
        conn.close()

    if tier_name and tier_name.strip().lower() == "auto" and len(updates_made) == 1:
        current, src = detect_model_tier(db_path=target_db)
        print(f"[OK] Model profile reset to AUTO. Current detected tier: {current.upper()} ({src})")
    elif tier_name and len(updates_made) == 1:
        norm_t = TIER_ALIASES.get(tier_name.strip().lower(), tier_name.strip().lower())
        print(f"[OK] Model profile explicitly set to: {norm_t.upper()}")
    elif updates_made:
        print(f"[OK] Updated runtime profile settings: {', '.join(updates_made)}")


def get_profile(cid: str = None, db_path: Path = None):
    """Displays current active model profile, effort level, account tier, and concurrency settings."""
    info = detect_model_and_pool(cid=cid, db_path=db_path)
    tier = info["model_tier"]
    efforts_str = " | ".join(e.capitalize() for e in info["supported_efforts"])
    print("\n================ SKILLSDB ACTIVE RUNTIME PROFILE ================")
    print(f"Account Quota Tier:  {info['account_tier'].upper()} ({info['account_tier_source']})")
    print(f"Active Model:        {info['model_name']} [Pool: {info['pool']}]")
    print(f"Reasoning Effort:    {info['effort_level'].upper()} (Supported: {efforts_str} | Weight: {info['quota_weight']}x)")
    print(f"Detected Tier:       {tier.upper()} ({info['tier_label']})")
    print(f"Detection Source:    {info['source']}")
    print(f"Quota Router Mode:   {info['quota_mode'].upper()}")
    acc_tier = info.get("account_tier", "ultra")
    if tier in [TIER_LOCAL, TIER_OFFLINE]:
        print("Concurrency Mode:    SOVEREIGN / LOCAL (Air-gapped offline mode)")
        print("Cloud Data Egress:   BLOCKED (Zero external network calls)")
        print("Inference Engine:    Local Codestral / Mistral / Ollama endpoint")
        local_status = check_local_endpoint()
        if local_status["available"]:
            models_str = ", ".join(local_status["models"]) if local_status["models"] else "Running (no tags)"
            print(f"Local Server:        Online at {local_status['url']} ({models_str})")
        else:
            print(f"Local Server:        Offline ({local_status['url']} unreachable)")
    elif acc_tier == "ultra" or tier == TIER_ULTRA:
        print("Concurrency Mode:    HIGH / ULTRA (Multi-threaded batching enabled)")
        print("Max Reader Workers:  16 parallel threads")
        print("Batch Retrieval:     Active (skillsdb get-skills / search-multi)")
        print("Skill Clusters:      Active (skillsdb get-cluster <domain>)")
    elif acc_tier == "pro" or tier == TIER_STANDARD:
        print("Concurrency Mode:    STANDARD / PRO (Balanced 8-thread Pro mode)")
        print("Max Reader Workers:  8 parallel threads")
        print("Batch Retrieval:     Active (skillsdb get-skills / search-multi)")
        print("Skill Clusters:      Active (skillsdb get-cluster <domain>)")
    else:
        print("Concurrency Mode:    LEAN / LITE (Token-conserving sequential mode)")
        print("Max Reader Workers:  4 parallel threads")
        print("Micro-Skills:        Active (Use --section to preserve quota)")
    print("=================================================================\n")
