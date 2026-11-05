"""
SkillsDB v4.0.2 (AntiGraviton Suite v2.0.2) Multi-Pool Quota Router, Golden-Ratio (61.8% / 85.0%) Ultra/Pro Control Plane,
Effort-Weighted 5h/24h Telemetry, Cross-Workstation Heartbeat Sync, Live Calibration Anchor, Context-Backpack Detector,
and External Gemini Context-Shield Offload Engine.
"""

import os
import re
import sys
import glob
import json
import time
import shutil
import socket
import sqlite3
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from ..config import (
    TIER_LEAN,
    TIER_STANDARD,
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
    POOL_EXT_CLI_OAUTH,
    POOL_EXT_AISTUDIO_API,
    POOL_EXT_WEB_HANDOVER,
    GEMINI_DIR,
    DB_PATH,
    resolve_user_home,
    estimate_tokens,
    __version__,
)
from .db import get_connection, init_db, managed_connection
from .detector import (
    ANTIGRAVITY_MODEL_CATALOG,
    parse_antigravity_model_spec,
    detect_model_and_pool,
    tier_to_display_label,
)
from .fim import extract_ast_contract_map

# In-memory O(1) stat caches keyed by file path -> (mtime_ns, size, ...)
_BACKPACK_CACHE: Dict[str, Tuple[int, int, Dict[str, Any]]] = {}
_TRANSCRIPT_5H_CACHE: Dict[str, Tuple[int, int, int, Dict[str, Any]]] = {}


ACCOUNT_TIER_BUDGETS = {
    "ultra": {
        "agy_5h_soft_limit_units": 600.0,
        "agy_5h_turn_limit": 200,
        "golden_ratio_pct": 61.8,
        "shield_pct": 85.0,
        "digest_line_threshold_base": 350,
        "digest_line_threshold_high_effort": 250,
        "digest_line_threshold_golden_ratio": 180,
        "max_workers": 16,
        "cli_daily_limit": 2000,
        "aistudio_daily_limit": 1500,
        "default_research_subagent_model": "flash",
    },
    "pro": {
        "agy_5h_soft_limit_units": 180.0,
        "agy_5h_turn_limit": 60,
        "golden_ratio_pct": 61.8,
        "shield_pct": 85.0,
        "digest_line_threshold_base": 200,
        "digest_line_threshold_high_effort": 140,
        "digest_line_threshold_golden_ratio": 120,
        "max_workers": 8,
        "cli_daily_limit": 1500,
        "aistudio_daily_limit": 1500,
        "default_research_subagent_model": "flash",
    },
}

EXTERNAL_GEMINI_MODELS = {
    "ultra": {
        "cli": "gemini-2.5-pro",
        "api": "gemini-2.5-pro",
    },
    "pro": {
        "cli": "gemini-2.5-pro",
        "api": "gemini-2.5-flash",
    },
}


def _resolve_workstation_id() -> str:
    """Resolves the live runtime workstation hostname (never hardcoded)."""
    env_ws = (os.environ.get("SKILLSDB_WORKSTATION_ID") or "").strip()
    if env_ws:
        return env_ws.upper()
    try:
        host = socket.gethostname().split(".")[0].strip()
        if host:
            return host.upper()
    except Exception:
        pass
    return (os.environ.get("COMPUTERNAME") or "LOCAL").strip().upper()


def _find_gemini_cli_binary() -> Optional[str]:
    """Locates the standalone @google/gemini-cli executable across PATH and npm global directories."""
    for candidate_name in ("gemini.cmd", "gemini.exe", "gemini"):
        found = shutil.which(candidate_name)
        if found:
            return found
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            npm_cmd = Path(appdata) / "npm" / "gemini.cmd"
            if npm_cmd.exists():
                return str(npm_cmd)
    return None


def _resolve_aistudio_api_key(db_path: Path = DB_PATH) -> Optional[str]:
    """Resolves GEMINI_API_KEY from environment variables or runtime_config."""
    for env_k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_k, "").strip()
        if val:
            return val
    try:
        if db_path and Path(db_path).exists():
            conn = get_connection(Path(db_path))
            try:
                cur = conn.cursor()
                cur.execute("SELECT value FROM runtime_config WHERE key = 'gemini_api_key';")
                row = cur.fetchone()
                if row and row[0] and row[0].strip():
                    return row[0].strip()
            finally:
                conn.close()
    except Exception:
        pass
    return None


def detect_external_pools(db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Detects availability and authentication readiness of External Pools 2, 3, and 4."""
    gemini_bin = _find_gemini_cli_binary()
    oauth_creds = GEMINI_DIR / "oauth_creds.json"
    google_accounts = GEMINI_DIR / "google_accounts.json"
    cli_oauth_ready = bool(gemini_bin) and (oauth_creds.exists() or google_accounts.exists())

    api_key = _resolve_aistudio_api_key(db_path=db_path)
    api_ready = bool(api_key)

    return {
        POOL_EXT_CLI_OAUTH: {
            "available": bool(gemini_bin),
            "authenticated": cli_oauth_ready,
            "binary": gemini_bin,
            "oauth_file": str(oauth_creds) if oauth_creds.exists() else None,
        },
        POOL_EXT_AISTUDIO_API: {
            "available": api_ready,
            "configured_in": "env" if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")) else ("runtime_config" if api_ready else None),
        },
        POOL_EXT_WEB_HANDOVER: {
            "available": True,
            "url": "https://gemini.google.com/app",
        },
    }


def _scan_single_transcript_5h(
    t_file: Path,
    cutoff_iso: str,
    cutoff_minute_bucket: int,
    settings_re: re.Pattern,
    max_tail_bytes: int = 3145728,
) -> Dict[str, Any]:
    """
    Scans a single transcript.jsonl for 5-hour model turns using O(1) binary tail-seek
    and an in-memory (mtime_ns, size, cutoff_minute_bucket) cache.
    """
    key = str(t_file)
    try:
        st = t_file.stat()
        mtime_ns = st.st_mtime_ns
        fsize = st.st_size
    except OSError:
        return {"turns": 0, "weighted_units": 0.0, "subagent_spawns": 0, "pools": {}}

    cached = _TRANSCRIPT_5H_CACHE.get(key)
    if cached and cached[0] == mtime_ns and cached[1] == fsize and cached[2] == cutoff_minute_bucket:
        return cached[3]

    file_pools = {
        POOL_AGY_GEMINI_HIGH: {"turns": 0, "weighted_units": 0.0, "models": {}},
        POOL_AGY_GEMINI_FAST: {"turns": 0, "weighted_units": 0.0, "models": {}},
        POOL_AGY_CLAUDE: {"turns": 0, "weighted_units": 0.0, "models": {}},
        POOL_AGY_OPEN_WEIGHT: {"turns": 0, "weighted_units": 0.0, "models": {}},
    }
    f_turns = 0
    f_weighted = 0.0
    f_subagents = 0
    active_spec = parse_antigravity_model_spec("Gemini 4 Argon (High)")

    try:
        with open(t_file, "rb") as bf:
            if fsize > max_tail_bytes:
                bf.seek(fsize - max_tail_bytes, os.SEEK_SET)
                raw = bf.read(max_tail_bytes)
                lines = raw.decode("utf-8", errors="ignore").splitlines()[1:]
            else:
                raw = bf.read()
                lines = raw.decode("utf-8", errors="ignore").splitlines()

        for line in lines:
            if "<USER_SETTINGS_CHANGE>" in line and "Model Selection" in line:
                for m_target in settings_re.findall(line):
                    parsed = parse_antigravity_model_spec(m_target)
                    if parsed:
                        active_spec = parsed
            if '"PLANNER_RESPONSE"' not in line and '"MODEL"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts_str = str(d.get("created_at") or "")
            if not ts_str or ts_str < cutoff_iso:
                continue
            src = d.get("source")
            stype = d.get("type")
            if src != "MODEL" and stype != "PLANNER_RESPONSE":
                continue

            pool_key = active_spec["pool"]
            base_weight = float(active_spec["quota_weight"])
            # Context-size multiplier: heavy turns with large tool/thinking payloads burn up to 1.35x base weight
            ctx_mult = min(1.35, 1.0 + max(0, len(line) - 4000) / 80000.0)
            weight = round(base_weight * ctx_mult, 2)
            model_lbl = active_spec["model_name"]

            if pool_key not in file_pools:
                pool_key = POOL_AGY_GEMINI_HIGH
            file_pools[pool_key]["turns"] += 1
            file_pools[pool_key]["weighted_units"] = round(file_pools[pool_key]["weighted_units"] + weight, 2)
            file_pools[pool_key]["models"][model_lbl] = file_pools[pool_key]["models"].get(model_lbl, 0) + 1

            f_turns += 1
            f_weighted = round(f_weighted + weight, 2)

            for tc in (d.get("tool_calls") or []):
                fn = (tc.get("name") or (tc.get("function") or {}).get("name") or "").lower()
                if fn == "invoke_subagent":
                    f_subagents += 1
    except Exception:
        pass

    res = {
        "turns": f_turns,
        "weighted_units": f_weighted,
        "subagent_spawns": f_subagents,
        "pools": file_pools,
    }
    if len(_TRANSCRIPT_5H_CACHE) > 64:
        _TRANSCRIPT_5H_CACHE.clear()
    _TRANSCRIPT_5H_CACHE[key] = (mtime_ns, fsize, cutoff_minute_bucket, res)
    return res


def record_remote_workstation_heartbeat(
    workstation_id: str,
    turns: int,
    weighted_units: float,
    pool: str = POOL_AGY_GEMINI_HIGH,
    subagent_spawns: int = 0,
    updated_at: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> None:
    """
    Persists or updates a workstation's 5-hour quota usage heartbeat in runtime_config
    ('quota_workstation_heartbeats') so multi-workstation users (Home vs. Enterprise PC)
    sharing or syncing customizations.db see unified 5-hour quota consumption.
    """
    if not db_path:
        return
    ws_clean = (workstation_id or "").strip().upper() or _resolve_workstation_id()
    now_iso = updated_at or datetime.now(timezone.utc).isoformat()
    try:
        with managed_connection(Path(db_path), init_schema=True) as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM runtime_config WHERE key = 'quota_workstation_heartbeats';")
            row = cur.fetchone()
            hb_map: Dict[str, Any] = {}
            if row and row[0]:
                try:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        hb_map = loaded
                except Exception:
                    hb_map = {}
            hb_map[ws_clean] = {
                "workstation_id": ws_clean,
                "updated_at": now_iso,
                "turns": int(turns),
                "weighted_units": round(float(weighted_units), 2),
                "pool": pool,
                "subagent_spawns": int(subagent_spawns),
            }
            cur.execute(
                "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('quota_workstation_heartbeats', ?, ?);",
                (json.dumps(hb_map), now_iso),
            )
    except Exception:
        pass


def _merge_and_sync_workstation_heartbeats(
    local_summary: Dict[str, Any],
    db_path: Optional[Path],
    window_hours: float = 5.0,
    persist_local: bool = True,
) -> List[str]:
    """
    Merges active 5-hour usage heartbeats from other workstations stored in runtime_config
    and optionally persists the current workstation's live 5h summary.
    """
    if not db_path or not Path(db_path).exists():
        return []
    current_ws = _resolve_workstation_id()
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    remote_active: List[str] = []

    try:
        conn = get_connection(Path(db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM runtime_config WHERE key = 'quota_workstation_heartbeats';")
            row = cur.fetchone()
            hb_map: Dict[str, Any] = {}
            if row and row[0]:
                try:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        hb_map = loaded
                except Exception:
                    hb_map = {}

            # 1. Merge remote workstations active within the rolling 5-hour window
            for ws_key, entry in list(hb_map.items()):
                if not isinstance(entry, dict):
                    continue
                ws_id = str(entry.get("workstation_id") or ws_key).strip().upper()
                ts_raw = str(entry.get("updated_at") or "").strip()
                if not ts_raw:
                    continue
                try:
                    dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    age_hours = max(0.0, (now_utc - dt).total_seconds() / 3600.0)
                except Exception:
                    continue
                if age_hours >= window_hours:
                    continue
                if ws_id == current_ws:
                    continue

                decay = max(0.0, (window_hours - age_hours) / window_hours)
                rem_turns = max(0, int(round(int(entry.get("turns") or 0) * decay)))
                rem_units = round(float(entry.get("weighted_units") or 0.0) * decay, 2)
                rem_subs = max(0, int(round(int(entry.get("subagent_spawns") or 0) * decay)))
                rem_pool = str(entry.get("pool") or POOL_AGY_GEMINI_HIGH)
                if rem_pool not in local_summary["pools"]:
                    rem_pool = POOL_AGY_GEMINI_HIGH

                if rem_units > 0 or rem_turns > 0:
                    local_summary["total_turns"] += rem_turns
                    local_summary["total_weighted_units"] = round(local_summary["total_weighted_units"] + rem_units, 2)
                    local_summary["subagent_spawns"] += rem_subs
                    local_summary["pools"][rem_pool]["turns"] += rem_turns
                    local_summary["pools"][rem_pool]["weighted_units"] = round(
                        local_summary["pools"][rem_pool]["weighted_units"] + rem_units, 2
                    )
                    remote_active.append(f"{ws_id} (+{rem_units:.1f}u)")

            # 2. Persist current workstation heartbeat when local turns change
            if persist_local:
                prev_local = hb_map.get(current_ws) or {}
                prev_turns = int(prev_local.get("turns") or -1)
                prev_units = float(prev_local.get("weighted_units") or -1.0)
                cur_local_turns = int(local_summary.get("local_turns", local_summary["total_turns"]))
                cur_local_units = float(local_summary.get("local_weighted_units", local_summary["total_weighted_units"]))
                if cur_local_turns != prev_turns or abs(cur_local_units - prev_units) >= 0.5:
                    hb_map[current_ws] = {
                        "workstation_id": current_ws,
                        "updated_at": now_iso,
                        "turns": cur_local_turns,
                        "weighted_units": round(cur_local_units, 2),
                        "pool": POOL_AGY_GEMINI_HIGH,
                        "subagent_spawns": int(local_summary.get("subagent_spawns") or 0),
                    }
                    cur.execute(
                        "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('quota_workstation_heartbeats', ?, ?);",
                        (json.dumps(hb_map), now_iso),
                    )
                    conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
    return remote_active


def calibrate_quota_load(
    calibrated_pct: Optional[float],
    db_path: Path = DB_PATH,
    cid: str = None,
    brain_dir: Path = None,
) -> Dict[str, Any]:
    """
    Sets or clears a live 5-hour quota percentage calibration anchor in runtime_config.
    Allows immediate alignment with the Antigravity UI quota gauge or cross-device usage:
      - calibrated_pct in [0.0..100.0]: anchors current 5h usage to calibrated_pct%, decaying
        linearly over the next 5 hours while adding any new turns executed after calibration.
      - calibrated_pct is None or < 0: clears the calibration anchor and returns to pure telemetry.
    """
    target_db = Path(db_path) if db_path else DB_PATH
    now_iso = datetime.now(timezone.utc).isoformat()
    if calibrated_pct is None or float(calibrated_pct) < 0:
        with managed_connection(target_db, init_schema=True) as conn:
            conn.execute("DELETE FROM runtime_config WHERE key = 'quota_calibration_anchor';")
        return {"calibrated": False, "cleared": True}

    pct_clamped = max(0.0, min(100.0, round(float(calibrated_pct), 1)))
    state = detect_model_and_pool(cid=cid, db_path=target_db)
    account_tier = "ultra" if state["account_tier"] == "ultra" else "pro"
    rolling = scan_rolling_5h_antigravity_usage(brain_dir=brain_dir, db_path=target_db)
    baseline_units = float(rolling["total_weighted_units"])

    anchor = {
        "calibrated_pct": pct_clamped,
        "calibrated_at": now_iso,
        "baseline_units": baseline_units,
        "account_tier": account_tier,
        "workstation_id": _resolve_workstation_id(),
    }
    with managed_connection(target_db, init_schema=True) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runtime_config (key, value, updated_at) VALUES ('quota_calibration_anchor', ?, ?);",
            (json.dumps(anchor), now_iso),
        )
    return {"calibrated": True, "anchor": anchor}


def _resolve_active_calibration(
    raw_total_5h_units: float,
    soft_limit: float,
    db_path: Optional[Path] = DB_PATH,
    window_hours: float = 5.0,
) -> Dict[str, Any]:
    """Evaluates whether an active 5-hour calibration anchor exists and computes effective 5h units."""
    default_cal = {
        "active": False,
        "calibrated_pct": None,
        "age_minutes": 0,
        "raw_5h_units": raw_total_5h_units,
        "effective_5h_units": raw_total_5h_units,
    }
    if not db_path or not Path(db_path).exists() or soft_limit <= 0:
        return default_cal

    try:
        conn = get_connection(Path(db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT value FROM runtime_config WHERE key = 'quota_calibration_anchor';")
            row = cur.fetchone()
            if not row or not row[0]:
                return default_cal
            anchor = json.loads(row[0])
            if not isinstance(anchor, dict):
                return default_cal
            ts_str = str(anchor.get("calibrated_at") or "").strip()
            cal_pct = float(anchor.get("calibrated_pct", -1.0))
            base_u = float(anchor.get("baseline_units", 0.0))
            if not ts_str or cal_pct < 0:
                return default_cal

            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_sec = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
            age_hours = age_sec / 3600.0
            if age_hours >= window_hours:
                return default_cal

            decay = max(0.0, (window_hours - age_hours) / window_hours)
            anchored_units = (cal_pct / 100.0) * soft_limit * decay
            delta_units = max(0.0, raw_total_5h_units - (base_u * decay))
            effective_units = round(anchored_units + delta_units, 2)
            return {
                "active": True,
                "calibrated_pct": round(cal_pct, 1),
                "calibrated_at": ts_str,
                "age_minutes": int(round(age_sec / 60.0)),
                "raw_5h_units": raw_total_5h_units,
                "effective_5h_units": effective_units,
            }
        finally:
            conn.close()
    except Exception:
        return default_cal


def scan_rolling_5h_antigravity_usage(
    brain_dir: Path = None,
    window_hours: float = 5.0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Scans Antigravity transcript.jsonl files modified within the rolling 5-hour window.
    Uses O(1) tail-seek and per-file mtime/size caching for sub-millisecond hook execution,
    and merges cross-workstation heartbeats when db_path is provided.
    """
    b_dir = Path(brain_dir) if brain_dir else (GEMINI_DIR / "antigravity" / "brain")
    now_utc = datetime.now(timezone.utc)
    cutoff_dt = now_utc - timedelta(hours=window_hours)
    cutoff_ts = cutoff_dt.timestamp()
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_minute_bucket = int(cutoff_ts // 60)

    pools = {
        POOL_AGY_GEMINI_HIGH: {"turns": 0, "weighted_units": 0.0, "models": {}},
        POOL_AGY_GEMINI_FAST: {"turns": 0, "weighted_units": 0.0, "models": {}},
        POOL_AGY_CLAUDE: {"turns": 0, "weighted_units": 0.0, "models": {}},
        POOL_AGY_OPEN_WEIGHT: {"turns": 0, "weighted_units": 0.0, "models": {}},
    }
    total_turns = 0
    total_weighted = 0.0
    subagent_spawns = 0

    if b_dir.exists():
        settings_re = re.compile(
            r"<USER_SETTINGS_CHANGE>(?:\\n|\s)*The user changed (?:the )?setting (?:\\[\"'`]|[\"'`])?Model Selection(?:\\[\"'`]|[\"'`])? from [^<]+? to ([^\n<\\]+?)\.(?=\s|\\n|<|\"|$)"
        )
        for folder in b_dir.iterdir():
            if not folder.is_dir() or folder.name == "tempmediaStorage":
                continue
            t_file = folder / ".system_generated" / "logs" / "transcript.jsonl"
            if not t_file.exists():
                continue
            try:
                if t_file.stat().st_mtime < cutoff_ts:
                    continue
            except OSError:
                continue

            f_res = _scan_single_transcript_5h(t_file, cutoff_iso, cutoff_minute_bucket, settings_re)
            total_turns += f_res["turns"]
            total_weighted = round(total_weighted + f_res["weighted_units"], 2)
            subagent_spawns += f_res["subagent_spawns"]
            for pk, pdata in f_res["pools"].items():
                if pk in pools:
                    pools[pk]["turns"] += pdata["turns"]
                    pools[pk]["weighted_units"] = round(pools[pk]["weighted_units"] + pdata["weighted_units"], 2)
                    for m_lbl, cnt in pdata["models"].items():
                        pools[pk]["models"][m_lbl] = pools[pk]["models"].get(m_lbl, 0) + cnt

    summary = {
        "window_hours": window_hours,
        "local_turns": total_turns,
        "local_weighted_units": total_weighted,
        "total_turns": total_turns,
        "total_weighted_units": total_weighted,
        "subagent_spawns": subagent_spawns,
        "pools": pools,
        "workstation_id": _resolve_workstation_id(),
        "remote_workstations_active": [],
    }
    if db_path is not None:
        persist_local = (brain_dir is None)
        summary["remote_workstations_active"] = _merge_and_sync_workstation_heartbeats(
            summary,
            db_path=db_path,
            window_hours=window_hours,
            persist_local=persist_local,
        )
    return summary


def record_quota_ledger_entry(
    pool: str,
    execution_tier: str,
    model_used: str,
    effort_level: str = "medium",
    input_tokens: int = 0,
    output_tokens: int = 0,
    saved_agy_tokens: int = 0,
    saved_agy_turns: int = 0,
    latency_ms: int = 0,
    status: str = "ok",
    task_summary: str = "",
    db_path: Path = DB_PATH,
) -> None:
    """Records an offload or subagent routing event in quota_ledger."""
    try:
        with managed_connection(db_path, init_schema=True) as conn:
            cur = conn.cursor()
            now_iso = datetime.now(timezone.utc).isoformat()
            cur.execute(
                """
                INSERT INTO quota_ledger (
                    pool, execution_tier, model_used, effort_level,
                    input_tokens, output_tokens, saved_agy_tokens, saved_agy_turns,
                    latency_ms, status, task_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    pool,
                    execution_tier,
                    model_used,
                    effort_level,
                    int(input_tokens),
                    int(output_tokens),
                    int(saved_agy_tokens),
                    int(saved_agy_turns),
                    int(latency_ms),
                    status,
                    (task_summary or "")[:240],
                    now_iso,
                ),
            )
    except Exception:
        pass


def measure_session_backpack(cid: str = None, brain_dir: Path = None) -> Dict[str, Any]:
    """
    Measures the active conversation's Context Backpack ('Rucksack-Gewicht') in O(1) time:
      - Uses binary tail-seek (up to 2 MB) + in-memory (mtime_ns, size) caching
      - Uncompacted steps and model turns since the last <CONTEXT_SUMMARY> compaction boundary
      - Estimated backpack token weight carried on every main-chat turn
      - Backpack classification ('LIGHT', 'MODERATE', 'HEAVY') and Contract-First Coding Split directive
    """
    b_dir = Path(brain_dir) if brain_dir else (GEMINI_DIR / "antigravity" / "brain")
    default_res = {
        "conversation_id": cid,
        "total_steps": 0,
        "uncompacted_steps": 0,
        "uncompacted_turns": 0,
        "backpack_tokens": 0,
        "backpack_state": "LIGHT",
        "coding_split_strategy": "DIRECT_ARGON (Backpack light: execute coupled & single-file edits directly in main chat on Gemini 4 Argon)",
    }
    if not b_dir.exists():
        return default_res

    t_file: Optional[Path] = None
    resolved_cid = cid
    if cid:
        cand = b_dir / cid / ".system_generated" / "logs" / "transcript.jsonl"
        if cand.exists():
            t_file = cand
        else:
            return default_res
    if t_file is None:
        newest_mtime = -1.0
        for folder in b_dir.iterdir():
            if not folder.is_dir() or folder.name == "tempmediaStorage":
                continue
            cand = folder / ".system_generated" / "logs" / "transcript.jsonl"
            if cand.exists():
                try:
                    mt = cand.stat().st_mtime
                    if mt > newest_mtime:
                        newest_mtime = mt
                        t_file = cand
                        resolved_cid = folder.name
                except OSError:
                    continue

    if t_file is None or not t_file.exists():
        return default_res

    cache_key = str(t_file)
    try:
        st = t_file.stat()
        mtime_ns = st.st_mtime_ns
        fsize = st.st_size
    except OSError:
        return default_res

    cached = _BACKPACK_CACHE.get(cache_key)
    if cached and cached[0] == mtime_ns and cached[1] == fsize:
        res_copy = dict(cached[2])
        res_copy["conversation_id"] = resolved_cid
        return res_copy

    max_tail_bytes = 2097152  # 2 MB tail window (> 500 uncompacted steps)
    total_steps = 0
    uncompacted_steps = 0
    uncompacted_turns = 0
    active_chars = 0

    try:
        with open(t_file, "rb") as bf:
            if fsize > max_tail_bytes:
                offset = fsize - max_tail_bytes
                bf.seek(offset, os.SEEK_SET)
                raw = bf.read(max_tail_bytes)
                lines = raw.decode("utf-8", errors="ignore").splitlines()[1:]
                avg_line_bytes = max(250, max_tail_bytes // max(1, len(lines)))
                total_steps = max(0, offset // avg_line_bytes)
            else:
                raw = bf.read()
                lines = raw.decode("utf-8", errors="ignore").splitlines()

        for line in lines:
            total_steps += 1
            if "<CONTEXT_SUMMARY>" in line:
                uncompacted_steps = 1
                uncompacted_turns = 0
                active_chars = min(len(line), 24000)
                continue
            uncompacted_steps += 1
            active_chars += min(len(line), 8000)
            if '"PLANNER_RESPONSE"' in line or '"source": "MODEL"' in line or '"source":"MODEL"' in line:
                uncompacted_turns += 1
    except Exception:
        return default_res

    backpack_tokens = max(1200, active_chars // 4)
    if uncompacted_steps >= 75 or backpack_tokens >= 60000:
        b_state = "HEAVY"
        strategy = (
            f"FRESH_ARGON_SPLIT (Backpack HEAVY: ~{backpack_tokens:,} tokens/turn across {uncompacted_steps} uncompacted steps! "
            "Keep interfaces & coupled logic in main chat, delegate isolated module/test files to invoke_subagent(TypeName='self', Model='pro') "
            "for 100% Argon coding in a fresh 0-token context, and run 'skillsdb ast-map <files>' before multi-file edits)"
        )
    elif uncompacted_steps >= 35 or backpack_tokens >= 28000:
        b_state = "MODERATE"
        strategy = (
            f"AST_CONTRACT_FIRST (Backpack MODERATE: ~{backpack_tokens:,} tokens/turn across {uncompacted_steps} steps. "
            "Run 'skillsdb ast-map <files>' for exact Lstart-Lend signatures before reading/editing multiple files)"
        )
    else:
        b_state = "LIGHT"
        strategy = (
            f"DIRECT_ARGON (Backpack LIGHT: ~{backpack_tokens:,} tokens/turn across {uncompacted_steps} steps. "
            "Execute coupled and single-file edits directly in main chat on Gemini 4 Argon)"
        )

    res = {
        "conversation_id": resolved_cid,
        "total_steps": total_steps,
        "uncompacted_steps": uncompacted_steps,
        "uncompacted_turns": uncompacted_turns,
        "backpack_tokens": backpack_tokens,
        "backpack_state": b_state,
        "coding_split_strategy": strategy,
    }
    if len(_BACKPACK_CACHE) > 64:
        _BACKPACK_CACHE.clear()
    _BACKPACK_CACHE[cache_key] = (mtime_ns, fsize, res)
    return res


def get_ledger_24h_summary(db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Queries quota_ledger for external 24h request counts and cumulative Antigravity savings."""
    summary = {
        "cli_24h_requests": 0,
        "aistudio_24h_requests": 0,
        "web_24h_handovers": 0,
        "subagent_24h_rewrites": 0,
        "ast_map_24h_calls": 0,
        "saved_agy_tokens_24h": 0,
        "saved_agy_turns_24h": 0,
        "saved_agy_tokens_total": 0,
        "saved_agy_turns_total": 0,
    }
    if not db_path or not Path(db_path).exists():
        return summary

    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        with managed_connection(Path(db_path), init_schema=True) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT pool, COUNT(*), COALESCE(SUM(saved_agy_tokens), 0), COALESCE(SUM(saved_agy_turns), 0)
                FROM quota_ledger
                WHERE created_at >= ? AND status = 'ok'
                GROUP BY pool;
                """,
                (cutoff_24h,),
            )
            for row in cur.fetchall():
                p_name, cnt, s_tok, s_turns = row[0], int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
                if p_name == POOL_EXT_CLI_OAUTH:
                    summary["cli_24h_requests"] += cnt
                elif p_name == POOL_EXT_AISTUDIO_API:
                    summary["aistudio_24h_requests"] += cnt
                elif p_name == POOL_EXT_WEB_HANDOVER:
                    summary["web_24h_handovers"] += cnt
                elif p_name == "agy_subagent_rewrite":
                    summary["subagent_24h_rewrites"] += cnt
                elif p_name == "local_ast_map":
                    summary["ast_map_24h_calls"] += cnt
                summary["saved_agy_tokens_24h"] += s_tok
                summary["saved_agy_turns_24h"] += s_turns

            cur.execute(
                """
                SELECT COALESCE(SUM(saved_agy_tokens), 0), COALESCE(SUM(saved_agy_turns), 0)
                FROM quota_ledger
                WHERE status = 'ok';
                """
            )
            tot_row = cur.fetchone()
            if tot_row:
                summary["saved_agy_tokens_total"] = int(tot_row[0] or 0)
                summary["saved_agy_turns_total"] = int(tot_row[1] or 0)
    except Exception:
        pass
    return summary


def compute_routing_decision(
    cid: str = None,
    db_path: Path = DB_PATH,
    model_name_hint: str = None,
    brain_dir: Path = None,
) -> Dict[str, Any]:
    """
    Computes the live Golden-Ratio ('Goldener Schnitt') routing policy for Ultra and Pro coding accounts:
      1. Account quota tier ('ultra' or 'pro' exclusively)
      2. Active Antigravity model family & internal pool (1A, 1B, 1C, 1D)
      3. Active reasoning effort level ('fast', 'medium', 'high')
      4. Rolling 5-hour Antigravity load (with cross-workstation sync + optional calibration anchor),
         Context Backpack weight, and 24-hour external pool budgets
    """
    state = detect_model_and_pool(cid=cid, db_path=db_path, model_name_hint=model_name_hint)
    account_tier = "ultra" if state["account_tier"] == "ultra" else "pro"
    budget = ACCOUNT_TIER_BUDGETS[account_tier]

    rolling_5h = scan_rolling_5h_antigravity_usage(brain_dir=brain_dir, db_path=db_path)
    backpack = measure_session_backpack(cid=cid, brain_dir=brain_dir)
    ledger_24h = get_ledger_24h_summary(db_path=db_path)
    ext_pools = detect_external_pools(db_path=db_path)

    active_pool = state["pool"]
    active_effort = state["effort_level"]
    configured_mode = state["quota_mode"]

    pool_5h_units = rolling_5h["pools"].get(active_pool, {}).get("weighted_units", 0.0)
    raw_total_5h_units = rolling_5h["total_weighted_units"]
    soft_limit = budget["agy_5h_soft_limit_units"]

    calibration = _resolve_active_calibration(raw_total_5h_units, soft_limit, db_path=db_path)
    total_5h_units = calibration["effective_5h_units"]
    rolling_5h["effective_weighted_units"] = total_5h_units
    load_pct = round(min(100.0, (total_5h_units / soft_limit) * 100.0), 1) if soft_limit > 0 else 100.0

    # Golden Ratio ('Goldener Schnitt': 61.8% / 85.0%) Operating Mode Cascade:
    # - < 61.8%: 'balanced' (Full Argon High power for coding & architecture)
    # - 61.8% - 85.0%: 'golden_ratio' (100% Argon coding, step Effort High -> Medium for standard coding, tighten digest offload)
    # - >= 85.0%: 'shield' (100% Argon/Claude coding, cross-pool failover to Claude 5.5 Pool 1C, max read-only offload)
    if configured_mode in ("balanced", "golden_ratio", "shield", "turbo", "direct"):
        effective_mode = configured_mode
    else:
        if load_pct >= budget["shield_pct"]:
            effective_mode = "shield"
        elif load_pct >= budget["golden_ratio_pct"]:
            effective_mode = "golden_ratio"
        else:
            effective_mode = "balanced"

    # Dynamic digest line threshold (tighter when High effort, Heavy Backpack, or Golden Ratio / Shield is active)
    if effective_mode == "shield" or backpack["backpack_state"] == "HEAVY":
        digest_line_threshold = min(100, budget["digest_line_threshold_golden_ratio"])
    elif effective_mode == "golden_ratio" or backpack["backpack_state"] == "MODERATE":
        digest_line_threshold = budget["digest_line_threshold_golden_ratio"]
    elif active_effort == EFFORT_HIGH:
        digest_line_threshold = budget["digest_line_threshold_high_effort"]
    else:
        digest_line_threshold = budget["digest_line_threshold_base"]

    # Subagent model targets (0-turn PreToolUse overwrite targets):
    # 1. Read-only 'research' subagents ALWAYS use full Gemini 3.8 Flash ('flash' in Pool 1B) on Ultra & Pro.
    subagent_research_model = budget["default_research_subagent_model"]

    # 2. Coding/implementation subagents (TypeName='self') NEVER downgrade to Flash!
    #    They stay 100% on Gemini 4 Argon ('inherit' when main is Argon, or 'pro' when main is Claude 5.5).
    if active_pool == POOL_AGY_CLAUDE:
        subagent_general_model = "pro"
    else:
        subagent_general_model = "inherit"

    # Golden Ratio step-by-step coding & quota optimization advice
    degradation_advice = ""
    if load_pct >= budget["golden_ratio_pct"] or backpack["backpack_state"] == "HEAVY":
        claude_turns = rolling_5h["pools"][POOL_AGY_CLAUDE]["turns"]
        steps_advice = [
            "Coding Guarantee: Keep 100% of code generation on Gemini 4 Argon (never Flash)"
        ]
        if backpack["backpack_state"] == "HEAVY":
            steps_advice.append(
                f"Backpack Guard ({backpack['uncompacted_steps']} steps, ~{backpack['backpack_tokens']:,} tok/turn): Use 'skillsdb ast-map <files>' for signatures and delegate isolated module/test coding to fresh invoke_subagent(TypeName='self', Model='pro')"
            )
        if active_effort == EFFORT_HIGH and EFFORT_MEDIUM in state["supported_efforts"]:
            steps_advice.append(
                f"Step 1 (Golden Ratio >= 62%): Switch {state['model_family']} Effort High (3.5x) -> Medium (2.2x) for standard coding (-37% burn, same Argon quality)"
            )
        if active_pool == POOL_AGY_GEMINI_HIGH and claude_turns == 0:
            steps_advice.append(
                "Step 2 (Cross-Pool Coding Reserve): Pool 1C (Claude Opus 5.5 / Sonnet 5.5) is at 0 turns in the 5h window"
            )
        degradation_advice = " | ".join(steps_advice)

    return {
        "state": state,
        "account_tier": account_tier,
        "configured_mode": configured_mode,
        "effective_mode": effective_mode,
        "digest_line_threshold": digest_line_threshold,
        "max_workers": budget["max_workers"],
        "subagent_research_model": subagent_research_model,
        "subagent_general_model": subagent_general_model,
        "rolling_5h": rolling_5h,
        "calibration": calibration,
        "backpack": backpack,
        "load_pct_5h": load_pct,
        "soft_limit_5h_units": soft_limit,
        "pool_5h_units": pool_5h_units,
        "ledger_24h": ledger_24h,
        "cli_daily_limit": budget["cli_daily_limit"],
        "aistudio_daily_limit": budget["aistudio_daily_limit"],
        "external_pools": ext_pools,
        "degradation_advice": degradation_advice,
    }


def format_hook_routing_banner(decision: Dict[str, Any]) -> str:
    """Formats a compact, high-signal routing directive banner for PreInvocation hook injection."""
    st = decision["state"]
    r5 = decision["rolling_5h"]
    cal = decision.get("calibration") or {}
    bp = decision["backpack"]
    l24 = decision["ledger_24h"]
    ext = decision["external_pools"]

    cli_state = "READY" if ext[POOL_EXT_CLI_OAUTH]["authenticated"] else ("INSTALLED" if ext[POOL_EXT_CLI_OAUTH]["available"] else "OFF")
    api_state = "READY" if ext[POOL_EXT_AISTUDIO_API]["available"] else "OFF"
    eff_units = r5.get("effective_weighted_units", r5["total_weighted_units"])
    cal_tag = f" [Calibrated {cal['calibrated_pct']}%]" if cal.get("active") else ""
    rem_ws = r5.get("remote_workstations_active") or []
    ws_tag = f" [Synced: {', '.join(rem_ws)}]" if rem_ws else ""

    lines = [
        f"[SKILLSDB MULTI-POOL QUOTA ROUTER: Account={decision['account_tier'].upper()} | Mode={decision['effective_mode'].upper()} | Active={st['model_name']} (Pool={st['pool']}, Weight={st['quota_weight']}x)]",
        f"  * 5h Antigravity Window: {r5['total_turns']} turns ({eff_units:.1f}/{decision['soft_limit_5h_units']:.0f} weighted units, {decision['load_pct_5h']:.0f}%{cal_tag}{ws_tag}) | Pool 1A Argon: {r5['pools'][POOL_AGY_GEMINI_HIGH]['turns']}t | Pool 1B Flash: {r5['pools'][POOL_AGY_GEMINI_FAST]['turns']}t | Pool 1C Claude: {r5['pools'][POOL_AGY_CLAUDE]['turns']}t | Pool 1D GPT-OSS: {r5['pools'][POOL_AGY_OPEN_WEIGHT]['turns']}t",
        f"  * Session Context Backpack: {bp['backpack_state']} (~{bp['backpack_tokens']:,} tok/turn, {bp['uncompacted_steps']} uncompacted steps) -> Strategy: {bp['coding_split_strategy']}",
        f"  * External Gemini Pools (24h): Pool 2 CLI-OAuth={cli_state} ({l24['cli_24h_requests']}/{decision['cli_daily_limit']} RPD) | Pool 3 AIStudio-API={api_state} ({l24['aistudio_24h_requests']}/{decision['aistudio_daily_limit']} RPD) | Saved: ~{l24['saved_agy_tokens_total']:,} AGY tokens ({l24['saved_agy_turns_total']} turns)",
        f"  * Golden Ratio Policy: 100% Argon/Claude for Coding (TypeName='self' -> '{decision['subagent_general_model']}'). Read-only 'research' subagents -> Model='{decision['subagent_research_model']}' (max {decision['max_workers']} workers). Offload read-only files > {decision['digest_line_threshold']} lines via 'skillsdb gemini-digest' or 'skillsdb ast-map <files>'.",
    ]
    if decision["degradation_advice"]:
        lines.append(f"  * Quota Optimization Advice: {decision['degradation_advice']}")
    return "\n".join(lines) + "\n"


def _classify_digest_task_tier(query: str, requested_tier: str, account_tier: str) -> str:
    """Determines whether an offloaded digest task requires 'ultra' or 'pro' execution."""
    req = (requested_tier or "auto").strip().lower()
    if req == "ultra":
        return "ultra"
    if req in ("pro", "standard", "lite", "lean"):
        return "pro"

    q_low = (query or "").lower()
    if any(k in q_low for k in ("root cause", "security", "vulnerability", "architecture", "race condition", "deadlock", "forensic")):
        return "ultra" if account_tier == "ultra" else "pro"
    return "pro"


def _call_aistudio_rest_api(prompt: str, model_name: str, api_key: str, timeout: float = 25.0) -> Tuple[bool, str]:
    """Calls Google AI Studio REST API (Pool 3) via stdlib urllib.request."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json", "User-Agent": f"SkillsDB-QuotaRouter/{__version__}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                candidates = body.get("candidates") or []
                if candidates:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    text_out = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
                    if text_out:
                        return True, text_out
        return False, "Empty response from AI Studio API"
    except Exception as err:
        return False, str(err)


def _call_gemini_cli_headless(prompt: str, model_name: str, gemini_bin: str, timeout: float = 60.0) -> Tuple[bool, str]:
    """Calls standalone @google/gemini-cli in headless non-interactive mode (Pool 2 OAuth)."""
    cmd = [gemini_bin, "-p", prompt, "-m", model_name, "--output-format", "json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw_out = proc.stdout.strip()
            try:
                parsed = json.loads(raw_out)
                if isinstance(parsed, dict):
                    resp_txt = parsed.get("response") or parsed.get("text") or parsed.get("output") or raw_out
                    return True, str(resp_txt).strip()
            except Exception:
                pass
            return True, raw_out
        return False, (proc.stderr or proc.stdout or f"Exit code {proc.returncode}").strip()[:300]
    except Exception as err:
        return False, str(err)


def _local_deterministic_digest(chunks: List[Tuple[str, str]], query: str, max_lines: int = 35) -> str:
    """
    Zero-dependency local deterministic Python AST / regex / log extractor fallback.
    Extracts matching keywords, error/exception lines, and symbol definitions with exact line numbers
    when offline or before external Pool 2/3 credentials are configured.
    """
    query_words = [
        w.lower()
        for w in re.findall(r"[A-Za-z0-9_]{3,}", query or "")
        if w.lower() not in ("the", "and", "for", "with", "from", "all", "list", "show", "find", "analyze", "summarize", "what", "where", "how")
    ]
    diag_re = re.compile(r"\b(?:error|exception|fail|failed|fatal|traceback|warning|critical|def |class )\b", re.IGNORECASE)

    findings = []
    for label, content in chunks:
        lines = content.splitlines()
        total_l = len(lines)
        matched_in_file = []
        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            low_l = stripped.lower()
            score = 0
            for qw in query_words:
                if qw in low_l:
                    score += 2
            if diag_re.search(stripped):
                score += 1
            if score > 0:
                matched_in_file.append((score, idx, stripped[:160]))

        matched_in_file.sort(key=lambda item: (-item[0], item[1]))
        top_matches = sorted(matched_in_file[: max(5, max_lines // max(1, len(chunks)))], key=lambda item: item[1])
        findings.append(f"### File: {label} ({total_l} lines)")
        if top_matches:
            for _sc, lno, snippet in top_matches:
                findings.append(f"  * L{lno}: {snippet}")
        else:
            head_preview = [f"  * L{i + 1}: {lines[i].strip()[:140]}" for i in range(min(5, total_l)) if lines[i].strip()]
            findings.extend(head_preview or ["  * (No matching lines)"])

    return "\n".join(findings)


def execute_gemini_digest(
    files: Optional[List[str]] = None,
    query: str = "Summarize key findings, errors, and exact line numbers.",
    execution_tier: str = "auto",
    preferred_pool: str = "auto",
    include_git_diff: bool = False,
    save_fact_key: Optional[str] = None,
    project_root: Optional[Path] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """
    Context-Shield Offload Executor:
    Reads large files, logs, or git diffs locally in Python, routes them to Pool 3 (AI Studio REST API)
    or Pool 2 (@google/gemini-cli OAuth) with automatic failover to the local deterministic extractor,
    and returns a surgical summary while recording saved Antigravity tokens & turns in quota_ledger.
    """
    start_t = time.perf_counter()
    decision = compute_routing_decision(db_path=db_path)
    account_tier = decision["account_tier"]
    task_tier = _classify_digest_task_tier(query, execution_tier, account_tier)

    collected_chunks: List[Tuple[str, str]] = []
    cwd = Path(project_root) if project_root else Path.cwd()

    if files:
        raw_matched: List[Path] = []
        for pattern in files:
            p_obj = Path(pattern)
            if p_obj.exists() and p_obj.is_file():
                raw_matched.append(p_obj)
            elif not p_obj.is_absolute() and (cwd / p_obj).exists() and (cwd / p_obj).is_file():
                raw_matched.append(cwd / p_obj)
            else:
                glob_matches = sorted(glob.glob(str(pattern), recursive=True))
                if not glob_matches and not p_obj.is_absolute():
                    glob_matches = sorted(glob.glob(str(cwd / pattern), recursive=True))
                raw_matched.extend(Path(m) for m in glob_matches if Path(m).is_file())
        name_counts: Dict[str, int] = {}
        for mp in raw_matched[:25]:
            name_counts[mp.name] = name_counts.get(mp.name, 0) + 1
        for mp in raw_matched[:25]:
            try:
                txt = mp.read_text(encoding="utf-8", errors="replace")
                lbl = mp.name
                if name_counts.get(mp.name, 0) > 1:
                    try:
                        lbl = mp.resolve().relative_to(cwd.resolve()).as_posix()
                    except Exception:
                        lbl = mp.as_posix()
                collected_chunks.append((lbl, txt))
            except Exception:
                pass

    if include_git_diff:
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            if proc.stdout.strip():
                collected_chunks.append(("git_diff_HEAD", proc.stdout))
        except Exception:
            pass

    if not collected_chunks:
        return {
            "success": False,
            "error": "No readable files or git diff content found to digest.",
            "pool_used": "none",
            "execution_tier": task_tier,
            "input_tokens": 0,
            "output_tokens": 0,
            "saved_agy_tokens": 0,
            "saved_agy_turns": 0,
            "digest": "",
        }

    formatted_corpus = []
    total_chars = 0
    for label, content in collected_chunks:
        numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(content.splitlines()[:4000]))
        block = f"=== FILE: {label} ===\n{numbered}\n"
        formatted_corpus.append(block)
        total_chars += len(content)

    full_prompt = (
        "You are a surgical code and log analysis co-processor for Google Antigravity.\n"
        "Respond strictly in concise English bullet points citing exact file names and line numbers (L<num>).\n"
        "Keep your response under 350 tokens so the main agent context stays minimal.\n\n"
        f"Task / Query: {query}\n\n"
        + "\n".join(formatted_corpus)
    )

    input_tokens = max(1, total_chars // 4)
    ext = decision["external_pools"]
    pool_pref = (preferred_pool or "auto").strip().lower()

    pool_used = "local_deterministic"
    model_used = "python-ast-log-extractor"
    digest_text = ""
    ok = False

    api_key = _resolve_aistudio_api_key(db_path=db_path)
    gemini_bin = ext[POOL_EXT_CLI_OAUTH]["binary"]
    models_for_tier = EXTERNAL_GEMINI_MODELS.get(task_tier, EXTERNAL_GEMINI_MODELS["pro"])

    # Routing order:
    # - If preferred_pool == 'api' or (preferred_pool == 'auto' and total_chars <= 120000 and api_key): try Pool 3 first, then Pool 2
    # - If preferred_pool == 'cli' or total_chars > 120000: try Pool 2 first, then Pool 3
    if pool_pref != "local" and decision["effective_mode"] != "direct":
        try_order = []
        if pool_pref == "api":
            try_order = ["api", "cli"]
        elif pool_pref == "cli":
            try_order = ["cli", "api"]
        elif total_chars <= 120000 and api_key:
            try_order = ["api", "cli"]
        else:
            try_order = ["cli", "api"]

        for candidate in try_order:
            if candidate == "api" and api_key:
                target_m = models_for_tier["api"]
                ok, out = _call_aistudio_rest_api(full_prompt, target_m, api_key)
                if ok:
                    pool_used = POOL_EXT_AISTUDIO_API
                    model_used = target_m
                    digest_text = out
                    break
            elif candidate == "cli" and gemini_bin and ext[POOL_EXT_CLI_OAUTH]["authenticated"]:
                target_m = models_for_tier["cli"]
                ok, out = _call_gemini_cli_headless(full_prompt, target_m, gemini_bin)
                if ok:
                    pool_used = POOL_EXT_CLI_OAUTH
                    model_used = target_m
                    digest_text = out
                    break

    if not ok or not digest_text:
        digest_text = _local_deterministic_digest(collected_chunks, query)
        pool_used = "local_deterministic"
        model_used = "python-ast-log-extractor"

    output_tokens = estimate_tokens(digest_text)
    saved_tokens = max(0, input_tokens - output_tokens)
    saved_turns = max(1, len(collected_chunks))
    latency_ms = int((time.perf_counter() - start_t) * 1000)

    record_quota_ledger_entry(
        pool=pool_used,
        execution_tier=task_tier,
        model_used=model_used,
        effort_level=decision["state"]["effort_level"],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        saved_agy_tokens=saved_tokens,
        saved_agy_turns=saved_turns,
        latency_ms=latency_ms,
        status="ok",
        task_summary=f"digest ({len(collected_chunks)} files): {query[:120]}",
        db_path=db_path,
    )

    if save_fact_key:
        try:
            from ..memory.project_memory import get_project_connection, find_project_root, mem_save_fact
            target_root = Path(project_root) if project_root else find_project_root()
            pconn = get_project_connection(target_root)
            try:
                compact_fact = re.sub(r"\s+", " ", digest_text).strip()[:600]
                mem_save_fact(pconn, save_fact_key, compact_fact, reason=f"Auto-saved via gemini-digest ({pool_used})")
            finally:
                pconn.close()
        except Exception:
            pass

    return {
        "success": True,
        "pool_used": pool_used,
        "model_used": model_used,
        "execution_tier": task_tier,
        "files_digested": [c[0] for c in collected_chunks],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "saved_agy_tokens": saved_tokens,
        "saved_agy_turns": saved_turns,
        "latency_ms": latency_ms,
        "saved_fact_key": save_fact_key,
        "digest": digest_text,
    }


def create_web_handover(
    topic: str,
    files: Optional[List[str]] = None,
    project_root: Optional[Path] = None,
    open_browser: bool = False,
    copy_clipboard: bool = True,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """
    Builds a self-contained Deep Research / Gemini Web (Pool 4: gemini.google.com) prompt bundle,
    copies it to the Windows clipboard, and records the handover in quota_ledger.
    """
    from ..memory.project_memory import get_project_connection, find_project_root

    root = Path(project_root) if project_root else find_project_root()
    facts_lines = []
    decisions_lines = []
    try:
        if (root / ".agents" / "memory.db").exists():
            pconn = get_project_connection(root)
            try:
                cur = pconn.cursor()
                cur.execute("SELECT key, value FROM project_facts WHERE deprecated_at IS NULL ORDER BY updated_at DESC LIMIT 8;")
                facts_lines = [f"- {r['key']}: {r['value']}" for r in cur.fetchall()]
                cur.execute("SELECT title, content FROM project_decisions WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 5;")
                decisions_lines = [f"- [{r['title']}]: {r['content']}" for r in cur.fetchall()]
            finally:
                pconn.close()
    except Exception:
        pass

    file_blocks = []
    total_file_tokens = 0
    if files:
        for fpat in files:
            fp = Path(fpat)
            if not fp.is_absolute() and not fp.exists():
                fp = root / fpat
            if fp.exists() and fp.is_file():
                try:
                    txt = fp.read_text(encoding="utf-8", errors="replace")[:20000]
                    total_file_tokens += estimate_tokens(txt)
                    file_blocks.append(f"### File: {fp.name}\n```\n{txt}\n```")
                except Exception:
                    pass

    bundle_lines = [
        "# Antigravity Pool 4 Deep Research / Web Handover Request",
        f"## Research & Analysis Topic\n{topic}",
        "## Output Formatting Requirements",
        "- Provide a structured, dense technical breakdown with exact root causes, API contracts, and actionable bullet points.",
        "- Conclude with a `## DISTILLED_FACTS_JSON` block mapping key architectural findings so `skillsdb gemini-ingest` can import them directly into `.agents/memory.db`.",
    ]
    if decisions_lines:
        bundle_lines.append("## Active Project Decisions\n" + "\n".join(decisions_lines))
    if facts_lines:
        bundle_lines.append("## Active Project Facts\n" + "\n".join(facts_lines))
    if file_blocks:
        bundle_lines.append("## Attached Workspace Excerpts\n" + "\n\n".join(file_blocks))

    prompt_bundle = "\n\n".join(bundle_lines)
    copied = False
    if copy_clipboard and os.name == "nt":
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                input=prompt_bundle,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=5,
                check=False,
            )
            copied = (proc.returncode == 0)
        except Exception:
            copied = False

    if open_browser and os.name == "nt":
        try:
            os.startfile("https://gemini.google.com/app")
        except Exception:
            pass

    est_saved_tokens = max(2500, total_file_tokens + 4000)
    record_quota_ledger_entry(
        pool=POOL_EXT_WEB_HANDOVER,
        execution_tier="ultra",
        model_used="gemini-web-deep-research",
        effort_level="high",
        input_tokens=estimate_tokens(prompt_bundle),
        output_tokens=0,
        saved_agy_tokens=est_saved_tokens,
        saved_agy_turns=5,
        latency_ms=0,
        status="ok",
        task_summary=f"web-handover: {topic[:120]}",
        db_path=db_path,
    )

    return {
        "topic": topic,
        "copied_to_clipboard": copied,
        "opened_browser": open_browser,
        "prompt_tokens": estimate_tokens(prompt_bundle),
        "estimated_saved_agy_tokens": est_saved_tokens,
        "prompt_bundle": prompt_bundle,
    }


def ingest_web_response(
    raw_text: Optional[str] = None,
    fact_key: str = "gemini_web_research_finding",
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ingests a copied Gemini Web / Deep Research response from the Windows clipboard into .agents/memory.db."""
    from ..memory.project_memory import get_project_connection, find_project_root, mem_save_fact

    content = raw_text
    if not content and os.name == "nt":
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
            if proc.returncode == 0:
                content = proc.stdout.strip()
        except Exception:
            content = ""

    if not content:
        return {"success": False, "error": "Clipboard is empty or no text provided."}

    root = Path(project_root) if project_root else find_project_root()
    compact = re.sub(r"\s+", " ", content).strip()[:700]
    pconn = get_project_connection(root)
    try:
        mem_save_fact(pconn, fact_key, compact, reason="Ingested from Pool 4 (Gemini Web / Deep Research)")
    finally:
        pconn.close()

    return {
        "success": True,
        "fact_key": fact_key,
        "ingested_tokens": estimate_tokens(content),
        "summary_preview": compact[:250],
    }


def execute_ast_map(
    files: List[str],
    symbol_filter: Optional[str] = None,
    include_private: bool = True,
    save_fact_key: Optional[str] = None,
    project_root: Optional[Path] = None,
    db_path: Path = DB_PATH,
) -> Dict[str, Any]:
    """
    Executes multi-file AST Contract & Line-Offset extraction, records token savings in
    quota_ledger (pool='local_ast_map'), and optionally persists the contract map in .agents/memory.db.
    """
    res = extract_ast_contract_map(
        files=files,
        symbol_filter=symbol_filter,
        include_private=include_private,
        project_root=project_root,
    )
    if not res.get("success"):
        return res

    record_quota_ledger_entry(
        pool="local_ast_map",
        execution_tier="pro",
        model_used="python-ast-contract-mapper",
        effort_level="medium",
        input_tokens=res["raw_tokens"],
        output_tokens=res["map_tokens"],
        saved_agy_tokens=res["saved_agy_tokens"],
        saved_agy_turns=res["saved_agy_turns"],
        latency_ms=res["latency_ms"],
        status="ok",
        task_summary=f"ast-map ({res['file_count']} files): {', '.join(res['files_mapped'][:5])}",
        db_path=db_path,
    )

    if save_fact_key:
        try:
            from ..memory.project_memory import get_project_connection, find_project_root, mem_save_fact
            target_root = Path(project_root) if project_root else find_project_root()
            pconn = get_project_connection(target_root)
            try:
                compact_fact = re.sub(r"\s+", " ", res["contract_map"]).strip()[:700]
                mem_save_fact(pconn, save_fact_key, compact_fact, reason="Auto-saved via skillsdb ast-map")
            finally:
                pconn.close()
        except Exception:
            pass

    res["saved_fact_key"] = save_fact_key
    return res


def print_quota_status(
    as_json: bool = False,
    cid: str = None,
    db_path: Path = DB_PATH,
    calibrate_pct: Optional[float] = None,
    reset_calibration: bool = False,
):
    """Displays the comprehensive 4-Pool & 13-Model/Effort Quota Router Dashboard (with optional 5h calibration)."""
    if reset_calibration:
        calibrate_quota_load(None, db_path=db_path, cid=cid)
        if not as_json:
            print("[OK] Cleared 5-hour quota calibration anchor (restored to pure transcript & workstation telemetry).")
    elif calibrate_pct is not None:
        cal_res = calibrate_quota_load(calibrate_pct, db_path=db_path, cid=cid)
        if not as_json and cal_res.get("calibrated"):
            anc = cal_res["anchor"]
            print(f"[OK] Anchored 5-hour quota window to {anc['calibrated_pct']}% on workstation {anc['workstation_id']}.")

    decision = compute_routing_decision(cid=cid, db_path=db_path)
    if as_json:
        print(json.dumps(decision, indent=2))
        return decision

    st = decision["state"]
    r5 = decision["rolling_5h"]
    cal = decision.get("calibration") or {}
    bp = decision["backpack"]
    l24 = decision["ledger_24h"]
    ext = decision["external_pools"]

    print("\n================ SKILLSDB MULTI-POOL QUOTA ROUTER ================")
    print(f"Account Quota Tier:   {decision['account_tier'].upper()} ({st['account_tier_source']})")
    print(f"Operating Mode:       {decision['effective_mode'].upper()} (Configured: {decision['configured_mode'].upper()})")
    print(f"Active Main Model:    {st['model_name']} [Pool: {st['pool']}]")
    eff_list = " / ".join(e.capitalize() for e in st["supported_efforts"])
    print(f"Reasoning Effort:     {st['effort_level'].upper()} (Supported: {eff_list} | Quota Weight: {st['quota_weight']}x)")
    print(f"Detection Source:     {st['source']}")

    print("\n--- Session Context Backpack & Contract-First Coding Split ---")
    print(f"  Backpack State:     {bp['backpack_state']} (~{bp['backpack_tokens']:,} tokens/turn | {bp['uncompacted_steps']} uncompacted steps, {bp['uncompacted_turns']} turns)")
    print(f"  Coding Split Rule:  {bp['coding_split_strategy']}")

    eff_units = r5.get("effective_weighted_units", r5["total_weighted_units"])
    print("\n--- Pool 1: Antigravity Internal 5-Hour Rolling Window ---")
    print(f"  Total 5h Activity:  {r5['total_turns']} turns ({eff_units:.1f} / {decision['soft_limit_5h_units']:.0f} weighted units -> {decision['load_pct_5h']:.1f}%)")
    if cal.get("active"):
        print(f"  Calibration Anchor: ACTIVE ({cal['calibrated_pct']}% set {cal['age_minutes']}m ago | Raw telemetry: {cal['raw_5h_units']:.1f}u)")
    if r5.get("remote_workstations_active"):
        print(f"  Synced Workstations: {', '.join(r5['remote_workstations_active'])} (Local host: {r5.get('workstation_id', 'LOCAL')})")
    print(f"  * Pool 1A (Gemini 4 Argon [Fast|Med|High]):      {r5['pools'][POOL_AGY_GEMINI_HIGH]['turns']:>3} turns ({r5['pools'][POOL_AGY_GEMINI_HIGH]['weighted_units']:.1f} units)")
    print(f"  * Pool 1B (Gemini 3.8 Flash [Fast|Med|High]):    {r5['pools'][POOL_AGY_GEMINI_FAST]['turns']:>3} turns ({r5['pools'][POOL_AGY_GEMINI_FAST]['weighted_units']:.1f} units, {r5['subagent_spawns']} subagents)")
    print(f"  * Pool 1C (Claude Opus/Sonnet 5.5 [Fast|Med|High]): {r5['pools'][POOL_AGY_CLAUDE]['turns']:>3} turns ({r5['pools'][POOL_AGY_CLAUDE]['weighted_units']:.1f} units)")
    print(f"  * Pool 1D (GPT-OSS 120B [Fixed Medium]):         {r5['pools'][POOL_AGY_OPEN_WEIGHT]['turns']:>3} turns ({r5['pools'][POOL_AGY_OPEN_WEIGHT]['weighted_units']:.1f} units)")

    print("\n--- External Gemini Offload Pools (24-Hour Rolling Window) ---")
    cli_info = ext[POOL_EXT_CLI_OAUTH]
    cli_tag = "READY (OAuth)" if cli_info["authenticated"] else ("INSTALLED (Run 'gemini' to login)" if cli_info["available"] else "NOT INSTALLED (npm i -g @google/gemini-cli)")
    api_info = ext[POOL_EXT_AISTUDIO_API]
    api_tag = f"READY ({api_info['configured_in']})" if api_info["available"] else "NOT CONFIGURED (Set GEMINI_API_KEY or 'skillsdb profile set --api-key <KEY>')"
    print(f"  * Pool 2 (@google/gemini-cli OAuth): {cli_tag:<42} | 24h Used: {l24['cli_24h_requests']} / {decision['cli_daily_limit']} RPD")
    print(f"  * Pool 3 (Google AI Studio REST):    {api_tag:<42} | 24h Used: {l24['aistudio_24h_requests']} / {decision['aistudio_daily_limit']} RPD")
    print(f"  * Pool 4 (Gemini Web / Deep Research): READY (Clipboard Handover Bridge)          | 24h Used: {l24['web_24h_handovers']} handovers")

    print("\n--- Active Routing Thresholds & Measured Offload Savings ---")
    print(f"  * Context-Shield Digest Threshold: > {decision['digest_line_threshold']} lines -> 'skillsdb gemini-digest --files <paths> --query \"...\"'")
    print(f"  * AST Contract Mapper:             'skillsdb ast-map <files>' ({l24.get('ast_map_24h_calls', 0)} maps in 24h)")
    print(f"  * Subagent PreToolUse Overwrite:   research -> Model='{decision['subagent_research_model']}' | coding (self) -> Model='{decision['subagent_general_model']}'")
    print(f"  * 24h Antigravity Quota Saved:     ~{l24['saved_agy_tokens_24h']:,} tokens ({l24['saved_agy_turns_24h']} turns avoided, {l24['subagent_24h_rewrites']} subagent rewrites)")
    print(f"  * All-Time Offload Quota Saved:    ~{l24['saved_agy_tokens_total']:,} tokens ({l24['saved_agy_turns_total']} turns avoided)")
    if decision["degradation_advice"]:
        print(f"  * Optimization Advice:             {decision['degradation_advice']}")
    print("==================================================================\n")
    return decision


# Canonical public API aliases
evaluate_quota_route = compute_routing_decision
prepare_web_handover = create_web_handover
ingest_web_handover = ingest_web_response
