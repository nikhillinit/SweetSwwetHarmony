"""Starwatcher Streamlit Backend Reference (v9.1.6)
Revised: 2026-02-18

This file is a *reference implementation* of the Python backend patterns
required by the Starwatcher integration contract.

Key goals:
- Correctness under Streamlit's rerun + iframe model
- Zero-lag interactivity using @st.fragment (when available)
- No rerun cascades for camera telemetry / toast dismissals
- One-shot initialCameraState to avoid snap-back on iframe remount
- Required ConstellationProps always passed (loadingState/error/fatalError/emptyState)
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Literal

import streamlit as st
import streamlit.components.v1 as components

try:
    # Streamlit raises this for invalid scoped reruns, etc.
    from streamlit.errors import StreamlitAPIException
except Exception:  # pragma: no cover
    StreamlitAPIException = Exception  # type: ignore


# -----------------------------------------------------------------------------
# 1) Declare the custom component (adjust build path)
# -----------------------------------------------------------------------------
starwatcher = components.declare_component(
    "starwatcher",
    path="frontend/build",  # <-- change to where your component build lives
)

RerunScope = Literal["app", "fragment", "none"]


# -----------------------------------------------------------------------------
# 2) Query-param helpers (st.query_params >= 1.30; fallback to experimental APIs)
# -----------------------------------------------------------------------------
def _get_query_param(key: str) -> Optional[str]:
    if hasattr(st, "query_params"):
        return st.query_params.get(key)
    qp = st.experimental_get_query_params()
    vals = qp.get(key, [])
    return vals[0] if vals else None


def _set_query_param(key: str, value: str) -> None:
    # NOTE: mutating query params triggers a rerun.
    if hasattr(st, "query_params"):
        st.query_params[key] = value
    else:
        st.experimental_set_query_params(**{key: value})


# -----------------------------------------------------------------------------
# 3) Encoding helpers (Share View)
# -----------------------------------------------------------------------------
def _b64url_encode(obj: Any) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> Any:
    padded = s + "=" * (-len(s) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    return json.loads(decoded)


def _default_camera() -> Dict[str, float]:
    return {"x": 0.0, "y": 0.0, "zoom": 1.0}


# -----------------------------------------------------------------------------
# 4) Toast helpers (server-side pruning + contract-safe payloads)
# -----------------------------------------------------------------------------
MAX_TOASTS = 20
TOAST_MAX_AGE_S = 30.0  # non-persistent toasts older than this are pruned
MAX_UNDO_DEPTH = 50
NAVIGATE_MIN_ZOOM = 1.6  # should be >= frontend "exploration" label threshold


def _new_toast(
    toast_type: str,
    message: str,
    *,
    action: Optional[Dict[str, Any]] = None,
    duration: Optional[int] = None,
) -> Dict[str, Any]:
    t: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": toast_type,
        "message": message,
        "_created_at": time.time(),  # internal only; stripped before sending to React
    }
    if action is not None:
        t["action"] = action
    if duration is not None:
        t["duration"] = duration
    return t


def _prune_toasts(toasts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = time.time()
    kept: List[Dict[str, Any]] = []
    for t in toasts:
        dur = t.get("duration", None)
        created = float(t.get("_created_at", now))
        # duration == 0 means persistent (keep)
        if dur == 0:
            kept.append(t)
            continue
        # if duration is None, frontend applies defaults; still treat as ephemeral
        if now - created < TOAST_MAX_AGE_S:
            kept.append(t)

    # Cap length to avoid ever-growing session_state payloads
    return kept[-MAX_TOASTS:]


def _sanitize_toasts(toasts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Ensure we only send contract fields to React (strip internal keys).
    cleaned: List[Dict[str, Any]] = []
    for t in toasts:
        cleaned.append({k: v for k, v in t.items() if k != "_created_at"})
    return cleaned


# -----------------------------------------------------------------------------
# 5) Minimal payload validation (prevents session_state corruption)
# -----------------------------------------------------------------------------
def _ensure_list(val: Any) -> List[Any]:
    return val if isinstance(val, list) else []


def _ensure_dict(val: Any) -> Dict[str, Any]:
    return val if isinstance(val, dict) else {}


def _ensure_str(val: Any) -> str:
    return val if isinstance(val, str) else ""


def _ensure_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except Exception:
        return default


# -----------------------------------------------------------------------------
# 6) Session-state init
# -----------------------------------------------------------------------------
def init_starwatcher_state() -> None:
    ss = st.session_state
    ss.setdefault("sw_filters_applied", [])
    ss.setdefault("sw_filters_draft", None)
    ss.setdefault("sw_filters_draft_source", None)  # 'user' | 'ai' | None

    ss.setdefault("sw_selected_node_ids", [])
    ss.setdefault("sw_isolated_node_id", None)
    ss.setdefault("sw_compare_node_ids", [])

    ss.setdefault("sw_camera_state", _default_camera())
    ss.setdefault("sw_initial_camera_state", None)  # one-shot on mount only
    ss.setdefault("sw_camera_command", None)        # one-shot programmatic camera moves

    ss.setdefault("sw_toasts", [])
    ss.setdefault("sw_last_event_id", None)

    ss.setdefault("sw_undo_stack", [])
    ss.setdefault("sw_redo_stack", [])

    ss.setdefault("sw_theme", "press_on_light")
    ss.setdefault("sw_reduced_motion", False)

    ss.setdefault("sw_loading_state", "idle")  # idle | initial_load | refreshing | transitioning
    ss.setdefault("sw_error_info", None)       # contract ErrorInfo | None
    ss.setdefault("sw_fatal_error", None)      # contract FatalError | None

    ss.setdefault("sw_notion_connected", False)  # used for initial empty state logic
    ss.setdefault("_sw_permalink_consumed", False)


# -----------------------------------------------------------------------------
# 7) Permalink restore (Share View)
# -----------------------------------------------------------------------------
def apply_permalink_if_present() -> None:
    ss = st.session_state
    if ss["_sw_permalink_consumed"]:
        return

    view_param = _get_query_param("view")
    if not view_param:
        return

    try:
        view_state = _b64url_decode(view_param)

        ss["sw_initial_camera_state"] = view_state.get("camera") or _default_camera()
        ss["sw_camera_state"] = ss["sw_initial_camera_state"]

        ss["sw_selected_node_ids"] = _ensure_list(view_state.get("selectedNodeIds"))
        ss["sw_isolated_node_id"] = view_state.get("isolatedNodeId", None)
        ss["sw_filters_applied"] = _ensure_list(view_state.get("filters"))

    except Exception as e:
        ss["sw_toasts"].append(_new_toast("error", f"Invalid share link: {e}", duration=0))
    finally:
        ss["_sw_permalink_consumed"] = True


# -----------------------------------------------------------------------------
# 8) Safe rerun helper (narrow exception handling)
# -----------------------------------------------------------------------------
def _safe_rerun(scope: Literal["app", "fragment"]) -> None:
    """Attempt scoped rerun; fall back to full rerun when unsupported."""
    try:
        st.rerun(scope=scope)  # Streamlit >= 1.38 supports scope param
    except (StreamlitAPIException, TypeError):
        st.rerun()


# -----------------------------------------------------------------------------
# 9) Event handler (React -> Python)
# Returns rerun scope: none | fragment | app
# -----------------------------------------------------------------------------
def handle_starwatcher_payload(
    payload: Dict[str, Any],
    *,
    nodes_by_id: Dict[str, Dict[str, Any]],
) -> RerunScope:
    ss = st.session_state

    if not payload or "eventId" not in payload or "event" not in payload:
        return "none"

    event_id = payload.get("eventId")
    if event_id is None or ss.get("sw_last_event_id") == event_id:
        return "none"
    ss["sw_last_event_id"] = event_id

    event = _ensure_dict(payload.get("event"))
    etype = _ensure_str(event.get("type"))

    # Keep backend camera synced even if camera_idle was coalesced away
    cam_snapshot = payload.get("cameraState")
    if isinstance(cam_snapshot, dict):
        ss["sw_camera_state"] = cam_snapshot

    # --- High-frequency telemetry (NO rerun) ---
    if etype == "camera_idle":
        camera_state = event.get("cameraState")
        if isinstance(camera_state, dict):
            ss["sw_camera_state"] = camera_state
        return "none"

    if etype == "dismiss_toast":
        toast_id = _ensure_str(event.get("toastId"))
        if toast_id:
            ss["sw_toasts"] = [t for t in ss["sw_toasts"] if t.get("id") != toast_id]
        return "none"

    # --- Selection ---
    if etype == "set_selection":
        ss["sw_selected_node_ids"] = _ensure_list(event.get("nodeIds"))
        if isinstance(event.get("cameraState"), dict):
            ss["sw_camera_state"] = event["cameraState"]
        return "fragment"

    if etype == "clear_selection":
        ss["sw_selected_node_ids"] = []
        if isinstance(event.get("cameraState"), dict):
            ss["sw_camera_state"] = event["cameraState"]
        return "fragment"

    if etype == "compare_selected":
        ids = _ensure_list(event.get("nodeIds"))
        ss["sw_compare_node_ids"] = ids[:5]  # enforce max compare size
        if isinstance(event.get("cameraState"), dict):
            ss["sw_camera_state"] = event["cameraState"]
        return "fragment"

    # --- Filters + history ---
    if etype == "apply_filters":
        new_filters = _ensure_list(event.get("filters"))
        provenance = _ensure_dict(event.get("provenance"))
        prov_action = _ensure_str(provenance.get("action"))

        ss["sw_undo_stack"].append(ss["sw_filters_applied"])
        if len(ss["sw_undo_stack"]) > MAX_UNDO_DEPTH:
            ss["sw_undo_stack"] = ss["sw_undo_stack"][-MAX_UNDO_DEPTH:]
        ss["sw_redo_stack"] = []

        ss["sw_filters_applied"] = new_filters

        # If a draft existed, clear it; suppress redundant toast when applying draft
        if ss.get("sw_filters_draft") is not None:
            ss["sw_filters_draft"] = None
            ss["sw_filters_draft_source"] = None
            if prov_action != "apply_draft":
                ss["sw_toasts"].append(_new_toast("info", "Stale draft filters discarded.", duration=4000))

        ss["sw_toasts"].append(_new_toast("success", "Filters applied.", duration=4000))
        return "app"

    if etype == "discard_draft":
        ss["sw_filters_draft"] = None
        ss["sw_filters_draft_source"] = None
        ss["sw_toasts"].append(_new_toast("info", "Draft filters discarded.", duration=4000))
        return "fragment"

    if etype == "undo":
        if ss["sw_undo_stack"]:
            prev = ss["sw_undo_stack"].pop()
            ss["sw_redo_stack"].append(ss["sw_filters_applied"])
            ss["sw_filters_applied"] = prev
            ss["sw_toasts"].append(_new_toast("info", "Undid last filter change.", duration=4000))
            return "app"
        return "none"

    if etype == "redo":
        if ss["sw_redo_stack"]:
            nxt = ss["sw_redo_stack"].pop()
            ss["sw_undo_stack"].append(ss["sw_filters_applied"])
            if len(ss["sw_undo_stack"]) > MAX_UNDO_DEPTH:
                ss["sw_undo_stack"] = ss["sw_undo_stack"][-MAX_UNDO_DEPTH:]
            ss["sw_filters_applied"] = nxt
            ss["sw_toasts"].append(_new_toast("info", "Redid filter change.", duration=4000))
            return "app"
        return "none"

    # --- Navigation / isolation ---
    if etype == "navigate_to_node":
        node_id = _ensure_str(event.get("nodeId"))
        node = nodes_by_id.get(node_id) if node_id else None
        if not node:
            ss["sw_toasts"].append(_new_toast("warning", "That company no longer exists.", duration=4000))
            return "fragment"

        ss["sw_selected_node_ids"] = [node_id]
        ss["sw_camera_command"] = {
            "x": _ensure_float(node.get("posX")),
            "y": _ensure_float(node.get("posY")),
            # Minimum zoom for navigate_to_node — should align with frontend label thresholds.
            "zoom": max(_ensure_float(ss["sw_camera_state"].get("zoom"), 1.0), NAVIGATE_MIN_ZOOM),
        }
        return "fragment"

    if etype == "isolate_node":
        node_id = _ensure_str(event.get("nodeId"))
        if node_id and node_id in nodes_by_id:
            ss["sw_isolated_node_id"] = node_id
            ss["sw_selected_node_ids"] = [node_id]
            return "fragment"
        return "none"

    if etype == "exit_isolation":
        ss["sw_isolated_node_id"] = None
        return "fragment"

    # --- Share View ---
    if etype == "request_share_link":
        view_state = _ensure_dict(event.get("viewState"))
        view_state.setdefault("camera", ss["sw_camera_state"])
        encoded = _b64url_encode(view_state)

        # Append toast BEFORE mutating query params (mutation triggers rerun)
        ss["sw_toasts"].append(_new_toast("success", "URL updated — copy the link from your browser bar.", duration=4000))
        _set_query_param("view", encoded)
        return "fragment"

    # --- Recovery / settings (examples) ---
    if etype == "retry":
        ss["sw_toasts"].append(_new_toast("info", "Retrying…", duration=4000))
        ss["_sw_force_reload"] = True
        return "app"

    if etype == "open_settings":
        ss["sw_toasts"].append(_new_toast("info", "Opening settings…", duration=4000))
        ss["sw_open_settings_target"] = _ensure_str(event.get("target", "general"))
        return "app"

    return "none"


# -----------------------------------------------------------------------------
# 10) Fragment render (fast reruns). Uses st.fragment if available.
# -----------------------------------------------------------------------------
_FRAGMENT_DECORATOR = getattr(st, "fragment", None)
def _fragment(fn):
    return fn if _FRAGMENT_DECORATOR is None else _FRAGMENT_DECORATOR(fn)


@_fragment
def render_constellation_workspace(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> None:
    ss = st.session_state

    # prune toasts server-side to prevent silent accumulation + reappearance
    ss["sw_toasts"] = _prune_toasts(_ensure_list(ss.get("sw_toasts")))
    toasts_for_react = _sanitize_toasts(ss["sw_toasts"])

    nodes_by_id = _index_by_id(nodes)

    # One-shot: apply initial camera only once (prevents snap-back on iframe remount)
    initial_cam = ss.pop("sw_initial_camera_state", None)
    camera_command = ss.pop("sw_camera_command", None)

    # REQUIRED props (contract): always pass them, even when null/idle
    loading_state = ss.get("sw_loading_state", "idle")
    error_info = ss.get("sw_error_info", None)
    fatal_error = ss.get("sw_fatal_error", None)

    empty_state = None
    if not nodes:
        if not ss.get("sw_notion_connected", False):
            empty_state = {
                "type": "initial",
                "title": "No data yet",
                "message": "Connect your Notion workspace to see your pipeline.",
                "action": {"label": "Connect Notion", "href": "/settings/notion", "target": "_top"},
            }
        elif ss.get("sw_filters_applied"):
            empty_state = {
                "type": "no_results",
                "title": "No companies match your filters",
                "message": "Try adjusting or clearing your filters.",
                "action": {
                    "label": "Clear Filters",
                    "event": {"type": "apply_filters", "filters": [], "provenance": {"action": "clear_filters"}},
                },
            }
        else:
            empty_state = {
                "type": "initial",
                "title": "No companies yet",
                "message": "Once your data syncs, companies will appear here.",
            }

    payload = starwatcher(
        # Core data
        nodes=nodes,
        edges=edges,

        # Required UI state (contract)
        loadingState=loading_state,
        error=error_info,
        fatalError=fatal_error,
        emptyState=empty_state,

        # Selection state
        selectedNodeIds=ss["sw_selected_node_ids"],
        isolatedNodeId=ss["sw_isolated_node_id"],

        # Theme + prefs
        theme=ss["sw_theme"],
        reducedMotion=ss["sw_reduced_motion"],

        # Camera
        initialCameraState=initial_cam,
        cameraState=camera_command,

        # Filters + history
        filters={
            "applied": ss["sw_filters_applied"],
            "draft": ss["sw_filters_draft"],
            "draftSource": ss["sw_filters_draft_source"],
        },
        canUndo=bool(ss["sw_undo_stack"]),
        canRedo=bool(ss["sw_redo_stack"]),

        # Toasts
        toasts=toasts_for_react,

        # Optional extras
        miniMap={"enabled": len(nodes) > 200, "size": "small", "position": "bottom-right", "opacity": 0.8},

        key="starwatcher_canvas",
        default=None,
    )

    if payload:
        scope = handle_starwatcher_payload(payload, nodes_by_id=nodes_by_id)
        if scope == "fragment":
            _safe_rerun("fragment")
        elif scope == "app":
            _safe_rerun("app")

    # Compare UI (side-by-side)
    compare_ids = _ensure_list(ss.get("sw_compare_node_ids"))
    compare_nodes = [nodes_by_id[nid] for nid in compare_ids if nid in nodes_by_id]
    if compare_nodes:
        st.divider()
        st.subheader("Compare")

        cols = st.columns(len(compare_nodes))
        for col, node in zip(cols, compare_nodes):
            with col:
                name = _ensure_str(node.get("name")) or "—"
                status_id = _ensure_str(node.get("status")) or "—"
                st.markdown(f"### {name}")
                st.caption(status_id)
                score = _ensure_float(node.get("thesisScore"), 0.0)
                st.metric("Thesis Score", f"{score:.0%}")
                rationale = _ensure_str(node.get("thesisRationale")) or "—"
                st.write(rationale)
                tags = node.get("tags") if isinstance(node.get("tags"), list) else []
                if tags:
                    st.write("**Tags:** " + ", ".join([_ensure_str(t) for t in tags if _ensure_str(t)]))


# -----------------------------------------------------------------------------
# 11) Main entrypoint (heavy data load outside fragment)
# -----------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(layout="wide")
    init_starwatcher_state()
    apply_permalink_if_present()

    # HEAVY LOAD OUTSIDE FRAGMENT:
    # Replace with your real loader. Use st.cache_data where possible.
    nodes = st.session_state.get("sw_nodes", [])
    edges = st.session_state.get("sw_edges", [])

    render_constellation_workspace(nodes, edges)


if __name__ == "__main__":
    main()
