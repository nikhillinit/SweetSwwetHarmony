"""
Pipeline Report 4A — generate report.json + self-contained report.html.

Single-page summary of pipeline health, convergence progress, feature wiring,
and activation gate status for operators.

Usage:
    python scripts/pipeline_report.py [--db signals.db] [--out <dir>] [--format html|json|both]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.git_utils import get_git_info

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ENV VAR ALLOWLIST (D8)
# ---------------------------------------------------------------------------
_FEATURE_FLAGS = [
    "DELIVERY_MODE", "V2_ENABLEMENT", "MERGE_WRITES_ENABLED",
    "LLM_THESIS_MODE", "ML_ENABLEMENT",
]

_API_KEYS = [
    "GITHUB_TOKEN", "GOOGLE_API_KEY", "NOTION_API_KEY", "GNEWS_API_KEY",
    "OPENAI_API_KEY", "PH_API_KEY", "PROXYCURL_API_KEY",
    "CRUNCHBASE_API_KEY", "OPENCORPORATES_API_KEY",
    "COMPANIES_HOUSE_API_KEY", "SLACK_WEBHOOK_URL",
]

_MAX_LIMIT = 25

# Schema policy: "pipeline-report-v1" is additive-compatible.
# New fields may appear in any section without a version bump.
# Version bump (v2) required only for field removals, type changes, or restructuring.


# ---------------------------------------------------------------------------
# GIT DIRTY CHECK (D11)
# ---------------------------------------------------------------------------
def _git_dirty(repo_root: str) -> Optional[bool]:
    """Return True if working tree is dirty, False if clean, None on failure."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, cwd=repo_root,
        )
        if result.returncode == 0:
            return len(result.stdout.strip()) > 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# DATA GATHERERS — all guard against missing tables (D5)
# ---------------------------------------------------------------------------

async def _gather_meta(db_path: str) -> Dict[str, Any]:
    """Report metadata: timestamps, git info, DB info."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    branch, sha = get_git_info(repo_root)
    dirty = _git_dirty(repo_root)

    db_size = None
    p = Path(db_path)
    if p.exists():
        db_size = p.stat().st_size

    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": "pipeline-report-v1",
        "git": {"branch": branch, "sha": sha, "dirty": dirty},
        "db": {"path": str(db_path), "size_bytes": db_size},
    }


async def _gather_readiness(db, db_path: str) -> Dict[str, Any]:
    """Activation gate snapshot — delegates verdict to check_activation_readiness (D4)."""
    from monitoring.activation_gate import check_activation_readiness
    from storage.signal_store import SignalStore

    section: Dict[str, Any] = {"available": True}

    # --- Canary (raw context for operators) ---
    try:
        cursor = await db.execute(
            """SELECT verdict, pass_rate, created_at, id
               FROM canary_runs
               ORDER BY created_at DESC, id DESC
               LIMIT 1"""
        )
        row = await cursor.fetchone()
        if row:
            created_at_str = row[2]
            run_age = None
            try:
                created = datetime.fromisoformat(created_at_str)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                run_age = round(
                    (datetime.now(timezone.utc) - created).total_seconds() / 3600, 1
                )
            except (ValueError, TypeError):
                pass
            section["canary"] = {
                "verdict": row[0], "pass_rate": row[1],
                "run_age_hours": run_age, "run_id": row[3],
            }
        else:
            section["canary"] = {"verdict": None, "pass_rate": None,
                                 "run_age_hours": None, "run_id": None}
    except Exception:
        section["canary"] = {"verdict": None, "pass_rate": None,
                             "run_age_hours": None, "run_id": None}

    # --- Drift alerts (raw context + details) ---
    try:
        cursor = await db.execute(
            """SELECT severity, COUNT(*)
               FROM canary_drift_alerts
               WHERE status = 'open'
               GROUP BY severity"""
        )
        rows = await cursor.fetchall()
        drift: Dict[str, Any] = {"open_critical": 0, "open_warning": 0, "details": []}
        for r in rows:
            if r[0] == "critical":
                drift["open_critical"] = r[1]
            elif r[0] == "warning":
                drift["open_warning"] = r[1]

        # Fetch detail rows (capped at 10, severity-ordered)
        try:
            cursor = await db.execute(
                """SELECT alert_type, severity, metric_name, message, created_at
                   FROM canary_drift_alerts
                   WHERE status = 'open'
                   ORDER BY CASE severity
                       WHEN 'critical' THEN 0
                       WHEN 'warning' THEN 1
                       WHEN 'info' THEN 2
                       ELSE 3
                   END, created_at DESC
                   LIMIT 10"""
            )
            detail_rows = await cursor.fetchall()
            drift["details"] = [
                {
                    "alert_type": dr[0],
                    "severity": dr[1],
                    "metric_name": dr[2],
                    "message": dr[3],
                    "created_at": dr[4],
                }
                for dr in detail_rows
            ]
        except Exception:
            pass  # Missing columns in older DBs — details stays []

        section["drift_alerts"] = drift
    except Exception:
        section["drift_alerts"] = {"open_critical": 0, "open_warning": 0, "details": []}

    # --- Multi-source count (D6 — json_array_length with fallback) ---
    try:
        try:
            cursor = await db.execute(
                """SELECT COUNT(*) FROM company_files
                   WHERE status = 'promoted'
                     AND json_array_length(source_apis) >= 2"""
            )
            row = await cursor.fetchone()
            ms_count = row[0] if row else 0
            ms_method = "json1"
        except Exception:
            # JSON1 fallback — count in Python
            cursor = await db.execute(
                "SELECT source_apis FROM company_files WHERE status = 'promoted'"
            )
            rows = await cursor.fetchall()
            ms_count = 0
            for r in rows:
                try:
                    apis = json.loads(r[0]) if r[0] else []
                    if len(apis) >= 2:
                        ms_count += 1
                except (json.JSONDecodeError, TypeError):
                    pass
            ms_method = "python_fallback"
        section["multi_source"] = {
            "promoted": ms_count, "threshold": 5, "method": ms_method,
        }
    except Exception:
        section["multi_source"] = {
            "promoted": 0, "threshold": 5, "method": "unavailable",
        }

    # --- Activation gate (D3 — short-lived SignalStore) ---
    try:
        store = SignalStore(db_path=db_path)
        await store.initialize()
        try:
            gate_result = await check_activation_readiness(store, step=3)
            gate_dict = gate_result.to_dict()
            section["activation_gate"] = gate_dict
            section["overall_verdict"] = gate_result.verdict
        finally:
            await store.close()
    except Exception as exc:
        section["activation_gate"] = {
            "verdict": "blocked", "error": str(exc),
        }
        section["overall_verdict"] = "blocked"

    return section


async def _gather_convergence(db) -> Dict[str, Any]:
    """Funnel metrics: company_files by status, source distribution, signals by source."""
    section: Dict[str, Any] = {"available": True}

    # Company files by status
    try:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM company_files GROUP BY status"
        )
        section["company_files_by_status"] = dict(await cursor.fetchall())
    except Exception:
        section["available"] = False
        section["company_files_by_status"] = {}

    # Promoted source distribution
    try:
        try:
            cursor = await db.execute(
                """SELECT json_array_length(source_apis) AS src_count, COUNT(*)
                   FROM company_files
                   WHERE status = 'promoted'
                   GROUP BY src_count
                   ORDER BY src_count"""
            )
            section["promoted_source_distribution"] = {
                str(r[0]): r[1] for r in await cursor.fetchall()
            }
        except Exception:
            # JSON1 fallback
            cursor = await db.execute(
                "SELECT source_apis FROM company_files WHERE status = 'promoted'"
            )
            dist: Dict[str, int] = {}
            for r in await cursor.fetchall():
                try:
                    n = len(json.loads(r[0]) if r[0] else [])
                except (json.JSONDecodeError, TypeError):
                    n = 0
                key = str(n)
                dist[key] = dist.get(key, 0) + 1
            section["promoted_source_distribution"] = dist
    except Exception:
        section["promoted_source_distribution"] = {}

    # Signals by source
    try:
        cursor = await db.execute(
            "SELECT source_api, COUNT(*) FROM signals GROUP BY source_api"
        )
        rows = await cursor.fetchall()
        section["signals_by_source"] = {r[0]: r[1] for r in rows}
        section["total_signals"] = sum(r[1] for r in rows)
        section["distinct_sources"] = sorted(r[0] for r in rows if r[0])
    except Exception:
        section["signals_by_source"] = {}
        section["total_signals"] = 0
        section["distinct_sources"] = []

    return section


async def _gather_pipeline_runs(db, limit: int = 10) -> Dict[str, Any]:
    """Recent pipeline runs with batch-fetched collector metrics (D7)."""
    limit = min(limit, _MAX_LIMIT)
    section: Dict[str, Any] = {"available": True}

    try:
        cursor = await db.execute(
            """SELECT run_id, started_at, completed_at, duration_seconds,
                      collectors_run, collectors_succeeded, collectors_failed,
                      signals_collected, signals_stored, signals_deduplicated,
                      signals_processed, signals_held, errors
               FROM pipeline_runs
               ORDER BY started_at DESC, id DESC
               LIMIT ?""",
            (limit,),
        )
        run_rows = await cursor.fetchall()
    except Exception:
        return {"available": False, "recent_runs": [], "totals": {}}

    if not run_rows:
        return {"available": True, "recent_runs": [], "totals": {
            "runs": 0, "failed_collectors": 0, "total_errors": 0,
        }}

    # Batch fetch collector metrics (D7 — no N+1)
    run_ids = [r[0] for r in run_rows]
    collector_map: Dict[str, List[Dict[str, Any]]] = {rid: [] for rid in run_ids}

    try:
        placeholders = ",".join("?" for _ in run_ids)
        cursor = await db.execute(
            f"""SELECT run_id, collector_name, status, signals_found,
                       api_calls, rate_limit_hits, retries, errors, error_messages
                FROM collector_metrics
                WHERE run_id IN ({placeholders})
                ORDER BY run_id, started_at""",
            run_ids,
        )
        for cr in await cursor.fetchall():
            collector_map[cr[0]].append({
                "name": cr[1], "status": cr[2], "signals_found": cr[3],
                "api_calls": cr[4], "rate_limit_hits": cr[5],
                "retries": cr[6], "errors": cr[7],
            })
    except Exception:
        pass  # collector_metrics may not exist; runs still available

    recent_runs = []
    total_failed = 0
    total_errors = 0
    productive_count = 0

    for r in run_rows:
        errors_raw = r[12]
        errors_list: List[str] = []
        if errors_raw:
            try:
                parsed = json.loads(errors_raw)
                if isinstance(parsed, list):
                    errors_list = [str(e) for e in parsed[:5]]
            except (json.JSONDecodeError, TypeError):
                pass

        # Ghost-run detection: no collectors, no signals, very fast
        collectors_run = r[4] or 0
        signals_collected = r[7] or 0
        duration = r[3]
        is_ghost = (
            collectors_run == 0
            and signals_collected == 0
            and duration is not None
            and duration < 1.0
        )

        run_entry = {
            "run_id": r[0], "started_at": r[1], "duration_seconds": r[3],
            "collectors_run": collectors_run, "collectors_succeeded": r[5],
            "collectors_failed": r[6], "signals_collected": signals_collected,
            "signals_stored": r[8], "signals_deduplicated": r[9],
            "signals_processed": r[10], "signals_held": r[11],
            "errors": errors_list,
            "collectors": collector_map.get(r[0], []),
            "is_ghost": is_ghost,
        }
        recent_runs.append(run_entry)
        total_failed += r[6] or 0
        total_errors += len(errors_list)
        if not is_ghost:
            productive_count += 1

    # Anomaly detection: check 3 most recent productive runs for triple-zero
    anomalies: List[str] = []
    productive_runs = [r for r in recent_runs if not r["is_ghost"]]
    for run in productive_runs[:3]:
        collected = run["signals_collected"] or 0
        stored = run["signals_stored"] or 0
        deduped = run["signals_deduplicated"] or 0
        if collected > 0 and stored == 0 and deduped == 0:
            anomalies.append(
                f"Signals collected but none stored or deduplicated in run "
                f"{run['run_id']} -- check write enablement or metrics wiring"
            )

    section["recent_runs"] = recent_runs
    section["anomalies"] = anomalies
    section["totals"] = {
        "runs": len(recent_runs),
        "productive_runs": productive_count,
        "ghost_run_definition": "collectors_run=0, signals_collected=0, duration<1s",
        "failed_collectors": total_failed,
        "total_errors": total_errors,
    }
    return section


async def _gather_phase_g(db) -> Dict[str, Any]:
    """Entity resolution status — all queries guarded (D5)."""
    enabled = os.getenv("USE_PHASE_G_IDENTITY_RESOLUTION", "false").lower() == "true"
    section: Dict[str, Any] = {"available": True, "enabled": enabled}

    for table, key in [
        ("entity_blocking_index", "blocking_index_count"),
        ("entity_aliases", "entity_aliases_count"),
        ("entity_migrations", "entity_migrations_count"),
    ]:
        try:
            cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            section[key] = row[0] if row else 0
        except Exception:
            section["available"] = False
            section[key] = 0

    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM claim_facts WHERE status = 'active'"
        )
        row = await cursor.fetchone()
        section["active_claim_facts"] = row[0] if row else 0
    except Exception:
        section["active_claim_facts"] = 0

    return section


async def _gather_warm_intro(db, db_path: str) -> Dict[str, Any]:
    """Warm intro wiring preflight."""
    mode = os.getenv("WARM_INTRO_NOTION_MODE", "off")
    enrichment = os.getenv("ENABLE_WARM_INTRO_ENRICHMENT", "false")
    graph_path = os.getenv("PRIVATE_GRAPH_DB_PATH", "")
    graph_exists = bool(graph_path) and Path(graph_path).exists()

    section: Dict[str, Any] = {
        "available": True,
        "private_graph_db_exists": graph_exists,
        "env_vars": {
            "WARM_INTRO_NOTION_MODE": mode,
            "ENABLE_WARM_INTRO_ENRICHMENT": enrichment,
            "PRIVATE_GRAPH_DB_PATH": graph_path or "(not set)",
        },
    }

    # Shadow log count
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM shadow_log WHERE feature_name = 'warm_intro_indicators'"
        )
        row = await cursor.fetchone()
        section["shadow_log_count"] = row[0] if row else 0
    except Exception:
        section["shadow_log_count"] = 0

    # Status logic
    if mode == "live" and enrichment.lower() == "true" and graph_exists:
        section["status"] = "live"
    elif mode == "shadow":
        section["status"] = "shadow"
    else:
        section["status"] = "not_wired"

    return section


_SECRET_SUBSTRINGS = {"KEY", "TOKEN", "SECRET", "WEBHOOK", "PASSWORD"}


def _is_secret_key(name: str) -> bool:
    """True if environment variable name likely holds a secret."""
    upper = name.upper()
    return any(s in upper for s in _SECRET_SUBSTRINGS)


def _gather_env_summary() -> Dict[str, Any]:
    """Feature flag snapshot + API key presence (D8 — no secrets).

    Distinguishes 'not set' (absent) from 'empty' (set to '').
    Redacts keys matching KEY/TOKEN/SECRET/WEBHOOK/PASSWORD.
    """
    flags = {}
    for key in _FEATURE_FLAGS:
        raw = os.environ.get(key)
        if raw is None:
            flags[key] = "(not set)"
        elif raw == "":
            flags[key] = "(empty)"
        else:
            flags[key] = raw

    keys = {}
    for key in _API_KEYS:
        raw = os.environ.get(key)
        if raw is None:
            keys[key] = "(not set)"
        elif raw == "":
            keys[key] = "(empty)"
        elif raw in ("xxx", "placeholder"):
            keys[key] = "(not set)"
        else:
            # Redact actual secret values
            keys[key] = "configured"

    return {
        "feature_flags": flags,
        "api_keys": keys,
        "note": (
            "Reflects the environment of the report-generating process, "
            "not the running pipeline service."
        ),
        "runtime": {
            "cwd": os.getcwd(),
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
        },
    }


# ---------------------------------------------------------------------------
# REPORT ASSEMBLY
# ---------------------------------------------------------------------------

async def generate_report(db_path: str, limit: int = 10) -> Dict[str, Any]:
    """Assemble the full pipeline report dict."""
    import aiosqlite

    meta = await _gather_meta(db_path)

    # Open read-only connection (D1)
    db = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = aiosqlite.Row
    try:
        readiness = await _gather_readiness(db, db_path)
        convergence = await _gather_convergence(db)
        pipeline_runs = await _gather_pipeline_runs(db, limit=limit)
        phase_g = await _gather_phase_g(db)
        warm_intro = await _gather_warm_intro(db, db_path)
    finally:
        await db.close()

    env_summary = _gather_env_summary()

    return {
        "schema_version": "pipeline-report-v1",
        "meta": meta,
        "readiness": readiness,
        "convergence": convergence,
        "pipeline_runs": pipeline_runs,
        "phase_g": phase_g,
        "warm_intro": warm_intro,
        "env_summary": env_summary,
    }


# ---------------------------------------------------------------------------
# HTML TEMPLATE (D2 — plain string with __REPORT_JSON__ placeholder)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline Report</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#16191D;color:#F1F3F5;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;line-height:1.5;padding:1.5rem}
h1{font-size:1.6rem;margin-bottom:.3rem}
h2{font-size:1.15rem;margin:1.2rem 0 .5rem;color:#A0A6AE;text-transform:uppercase;letter-spacing:.05em}
.banner{padding:.8rem 1.2rem;border-radius:6px;margin-bottom:1.5rem;font-weight:600;font-size:1.05rem}
.banner.green{background:#1B4332;border:1px solid #2DB299}
.banner.yellow{background:#3D3400;border:1px solid #D4A017}
.banner.red{background:#4A1010;border:1px solid #E74C3C}
.ts{color:#6C757D;font-size:.85rem;font-weight:400}
.card{background:#1E2228;border:1px solid #2A2F36;border-radius:6px;padding:1rem;margin-bottom:1rem}
.kv{display:flex;justify-content:space-between;padding:.25rem 0;border-bottom:1px solid #2A2F36}
.kv:last-child{border-bottom:none}
.kv .k{color:#A0A6AE}
.kv .v{color:#F1F3F5;font-family:'Cascadia Code','Fira Code',monospace;font-size:.9rem}
.badge{display:inline-block;padding:.1rem .45rem;border-radius:3px;font-size:.8rem;font-weight:600}
.badge.pass{background:#1B4332;color:#2DB299}
.badge.warn{background:#3D3400;color:#D4A017}
.badge.fail{background:#4A1010;color:#E74C3C}
.badge.off{background:#2A2F36;color:#6C757D}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;padding:.4rem .6rem;color:#A0A6AE;border-bottom:1px solid #2A2F36}
td{padding:.4rem .6rem;border-bottom:1px solid #1E2228}
.empty{color:#6C757D;font-style:italic;padding:1rem}
.ghost{opacity:0.5}
.anomaly-banner{background:#3D2800;border:1px solid #D4A017;border-radius:6px;padding:.6rem 1rem;margin-bottom:.8rem;color:#F5C842;font-size:.9rem}
</style>
</head>
<body>
<script type="application/json" id="report-data">__REPORT_JSON__</script>
<div id="app"></div>
<script>
(function(){
var data=JSON.parse(document.getElementById('report-data').textContent);
var app=document.getElementById('app');
function el(tag,attrs,children){
  var e=document.createElement(tag);
  if(attrs)for(var k in attrs){if(k==='cls')e.className=attrs[k];else e.setAttribute(k,attrs[k])}
  if(typeof children==='string')e.textContent=children;
  else if(Array.isArray(children))children.forEach(function(c){if(c)e.appendChild(c)});
  return e;
}
function kv(k,v){
  return el('div',{cls:'kv'},[el('span',{cls:'k'},k),el('span',{cls:'v'},String(v!=null?v:'—'))]);
}
function badge(text,type){return el('span',{cls:'badge '+type},text)}
function section(title,content){
  var s=el('div',null,[el('h2',null,title)]);
  if(Array.isArray(content))content.forEach(function(c){s.appendChild(c)});
  else s.appendChild(content);
  return s;
}

// Banner
var v=data.readiness&&data.readiness.overall_verdict||'blocked';
var bc=v==='ready'?'green':v==='warn'?'yellow':'red';
var bl=v==='ready'?'READY':v==='warn'?'WARNING':'BLOCKED';
app.appendChild(el('h1',null,'Pipeline Report'));
app.appendChild(el('div',{cls:'ts'},data.meta.generated_at_utc||''));
app.appendChild(el('div',{cls:'banner '+bc},'Overall: '+bl+(data.readiness.activation_gate&&data.readiness.activation_gate.reasons?' — '+data.readiness.activation_gate.reasons.join('; '):'')));

// Readiness
var rc=el('div',{cls:'card'});
var rd=data.readiness||{};
if(rd.canary)rc.appendChild(kv('Canary verdict',rd.canary.verdict||'none'));
if(rd.canary)rc.appendChild(kv('Canary pass rate',rd.canary.pass_rate!=null?rd.canary.pass_rate.toFixed(4):'—'));
if(rd.canary)rc.appendChild(kv('Canary age (hours)',rd.canary.run_age_hours!=null?rd.canary.run_age_hours:'—'));
if(rd.drift_alerts){rc.appendChild(kv('Open critical alerts',rd.drift_alerts.open_critical));rc.appendChild(kv('Open warning alerts',rd.drift_alerts.open_warning));
  if(rd.drift_alerts.details&&rd.drift_alerts.details.length>0){
    var dt=el('table');
    dt.appendChild(el('tr',null,[el('th',null,'Type'),el('th',null,'Severity'),el('th',null,'Metric'),el('th',null,'Message'),el('th',null,'Created')]));
    rd.drift_alerts.details.forEach(function(d){
      dt.appendChild(el('tr',null,[el('td',null,d.alert_type||''),el('td',null,d.severity||''),el('td',null,d.metric_name||''),el('td',null,d.message||''),el('td',null,d.created_at||'')]));
    });
    rc.appendChild(dt);
  }
}
if(rd.multi_source){rc.appendChild(kv('Multi-source promoted',rd.multi_source.promoted+' / '+rd.multi_source.threshold+' threshold'));rc.appendChild(kv('Multi-source method',rd.multi_source.method))}
app.appendChild(section('Readiness',rc));

// Convergence
var cv=data.convergence||{};
var cc=el('div',{cls:'card'});
if(cv.available===false){cc.appendChild(el('div',{cls:'empty'},'Convergence data not available'))}
else{
  cc.appendChild(kv('Total signals',cv.total_signals||0));
  var cfs=cv.company_files_by_status||{};
  for(var s in cfs)cc.appendChild(kv('Company files: '+s,cfs[s]));
  var psd=cv.promoted_source_distribution||{};
  for(var n in psd)cc.appendChild(kv(n+'-source promoted',psd[n]));
  cc.appendChild(kv('Distinct sources',(cv.distinct_sources||[]).join(', ')||'none'));
}
app.appendChild(section('Convergence',cc));

// Pipeline runs
var pr=data.pipeline_runs||{};
var pc=el('div',null);
if(!pr.available||!pr.recent_runs||pr.recent_runs.length===0){pc.appendChild(el('div',{cls:'card'},[el('div',{cls:'empty'},'No pipeline runs recorded')]))}
else{
  // Anomaly banner
  if(pr.anomalies&&pr.anomalies.length>0){
    pr.anomalies.forEach(function(a){pc.appendChild(el('div',{cls:'anomaly-banner'},a))});
  }
  var totc=el('div',{cls:'card'});
  totc.appendChild(kv('Runs',pr.totals.productive_runs+' productive / '+pr.totals.runs+' total'));
  totc.appendChild(kv('Failed collectors (total)',pr.totals.failed_collectors));
  totc.appendChild(kv('Errors (total)',pr.totals.total_errors));
  pc.appendChild(totc);
  pr.recent_runs.forEach(function(run){
    var rc2=el('div',{cls:'card'+(run.is_ghost?' ghost':'')});
    rc2.appendChild(kv('Run ID',run.run_id+(run.is_ghost?' (ghost)':'')));
    rc2.appendChild(kv('Started',run.started_at));
    rc2.appendChild(kv('Duration (s)',run.duration_seconds!=null?run.duration_seconds.toFixed(1):'—'));
    rc2.appendChild(kv('Signals collected',run.signals_collected));
    rc2.appendChild(kv('Signals stored',run.signals_stored));
    rc2.appendChild(kv('Signals deduped',run.signals_deduplicated));
    rc2.appendChild(kv('Collectors',run.collectors_run+' run, '+run.collectors_succeeded+' ok, '+run.collectors_failed+' failed'));
    if(run.collectors&&run.collectors.length>0){
      var tbl=el('table');
      var hdr=el('tr',null,[el('th',null,'Collector'),el('th',null,'Status'),el('th',null,'Signals'),el('th',null,'API calls'),el('th',null,'Retries')]);
      tbl.appendChild(hdr);
      run.collectors.forEach(function(c){
        var tr=el('tr',null,[el('td',null,c.name),el('td',null,c.status),el('td',null,String(c.signals_found)),el('td',null,String(c.api_calls)),el('td',null,String(c.retries))]);
        tbl.appendChild(tr);
      });
      rc2.appendChild(tbl);
    }
    pc.appendChild(rc2);
  });
}
app.appendChild(section('Recent Pipeline Runs',pc));

// Phase G
var pg=data.phase_g||{};
var pgc=el('div',{cls:'card'});
if(pg.available===false){pgc.appendChild(el('div',{cls:'empty'},'Phase G tables not available'))}
else{
  pgc.appendChild(kv('Enabled',pg.enabled?'yes':'no'));
  pgc.appendChild(kv('Blocking index entries',pg.blocking_index_count||0));
  pgc.appendChild(kv('Entity aliases',pg.entity_aliases_count||0));
  pgc.appendChild(kv('Entity migrations',pg.entity_migrations_count||0));
  pgc.appendChild(kv('Active claim facts',pg.active_claim_facts||0));
}
app.appendChild(section('Phase G Entity Resolution',pgc));

// Warm intro
var wi=data.warm_intro||{};
var wic=el('div',{cls:'card'});
wic.appendChild(kv('Status',wi.status||'unknown'));
wic.appendChild(kv('Private graph DB exists',wi.private_graph_db_exists?'yes':'no'));
wic.appendChild(kv('Shadow log entries',wi.shadow_log_count||0));
var wie=wi.env_vars||{};
for(var ek in wie)wic.appendChild(kv(ek,wie[ek]));
app.appendChild(section('Warm Intro',wic));

// Env summary
var es=data.env_summary||{};
var esc=el('div',{cls:'card'});
if(es.note)esc.appendChild(kv('Note',es.note));
var ff=es.feature_flags||{};
for(var fk in ff)esc.appendChild(kv(fk,ff[fk]));
var ak=es.api_keys||{};
for(var akk in ak)esc.appendChild(kv(akk,ak[akk]));
if(es.runtime){
  esc.appendChild(kv('Python',es.runtime.python_version||'—'));
  esc.appendChild(kv('Platform',es.runtime.platform||'—'));
  esc.appendChild(kv('CWD',es.runtime.cwd||'—'));
}
app.appendChild(section('Environment (Report Process)',esc));

// Git
var gm=data.meta&&data.meta.git||{};
var gc=el('div',{cls:'card'});
gc.appendChild(kv('Branch',gm.branch||'—'));
gc.appendChild(kv('SHA',gm.sha||'—'));
gc.appendChild(kv('Dirty',gm.dirty===true?'yes':gm.dirty===false?'no':'unknown'));
gc.appendChild(kv('DB size (bytes)',data.meta&&data.meta.db?data.meta.db.size_bytes:'—'));
app.appendChild(section('Meta',gc));
})();
</script>
</body>
</html>"""


def render_html(report: Dict[str, Any]) -> str:
    """Render report dict into self-contained HTML (D2, D9, D10)."""
    json_str = json.dumps(report, indent=2, default=str)
    # D10: prevent </script> injection
    json_str = json_str.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__REPORT_JSON__", json_str)


# ---------------------------------------------------------------------------
# ATOMIC WRITE
# ---------------------------------------------------------------------------

def _write_atomic(path: str, content: str) -> None:
    """Write via tmp + rename for atomicity."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI ENTRYPOINT
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> Dict[str, Any]:
    report = await generate_report(args.db, limit=args.limit)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or os.path.join("artifacts", "reports", "pipeline", ts)
    os.makedirs(out_dir, exist_ok=True)

    fmt = args.format

    if fmt in ("json", "both"):
        json_path = os.path.join(out_dir, "report.json")
        _write_atomic(json_path, json.dumps(report, indent=2, default=str))
        print(f"Wrote {json_path}")

    if fmt in ("html", "both"):
        html_path = os.path.join(out_dir, "report.html")
        _write_atomic(html_path, render_html(report))
        print(f"Wrote {html_path}")

    verdict = report.get("readiness", {}).get("overall_verdict", "unknown")
    print(f"Overall verdict: {verdict.upper()}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Pipeline Report 4A")
    parser.add_argument(
        "--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Database path (default: DISCOVERY_DB_PATH or signals.db)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output directory (default: artifacts/reports/pipeline/<timestamp>/)",
    )
    parser.add_argument(
        "--format", choices=["html", "json", "both"], default="both",
        help="Output format (default: both)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max pipeline runs to include (default: 10, max: 25)",
    )
    args = parser.parse_args()
    args.limit = min(args.limit, _MAX_LIMIT)

    if sys.platform == "win32":
        sys.stdout.reconfigure(errors="replace")

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
