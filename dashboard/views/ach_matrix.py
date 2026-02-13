"""
ACH Matrix Grid View -- interactive hypothesis x evidence analysis display.

Features:
- Color-coded hypothesis x evidence grid via st.dataframe
- Hypothesis score summary row with top hypothesis highlight
- Master-detail: select an evidence row to see supports/opposes breakdown
- Narrative panel with bull/bear summaries and differentiator badges
- CSV export of the full matrix
"""

from __future__ import annotations

import io
import re
from typing import Optional

import pandas as pd
import streamlit as st


# =============================================================================
# CONSTANTS
# =============================================================================

_SCORE_LABELS = {1: "+1", 0: "0", -1: "-1", None: "N/A"}

_SCORE_COLORS = {
    "+1": "background-color: #c6efce; color: #006100",  # green
    "0": "background-color: #f2f2f2; color: #595959",   # gray
    "-1": "background-color: #ffc7ce; color: #9c0006",  # red
    "N/A": "background-color: #e8e8e8; color: #999999", # light gray
}

_SCORE_TOTAL_POSITIVE = "background-color: #c6efce; color: #006100; font-weight: bold"
_SCORE_TOTAL_NEGATIVE = "background-color: #ffc7ce; color: #9c0006; font-weight: bold"
_SCORE_TOTAL_NEUTRAL = "background-color: #f2f2f2; color: #595959; font-weight: bold"


# =============================================================================
# PUBLIC API
# =============================================================================

def render_ach_view(
    ach_data: dict,
    tribunal_data: Optional[dict] = None,
) -> None:
    """Render the interactive ACH matrix view.

    Args:
        ach_data: ACH analysis dict with hypotheses, evidence, cells, and scores.
        tribunal_data: Optional tribunal narrative dict with bull/bear summaries
                       and differentiators.
    """
    if not ach_data:
        st.info("No ACH analysis data available.")
        return

    hypotheses = ach_data.get("hypotheses", [])
    evidence = ach_data.get("evidence", [])
    cells = ach_data.get("cells", [])
    hypothesis_scores = ach_data.get("hypothesis_scores", {})
    top_hypothesis = ach_data.get("top_hypothesis")
    top_score = ach_data.get("top_score")
    evidence_count = ach_data.get("evidence_count", 0)

    if not hypotheses or not evidence:
        st.warning("ACH data is incomplete: missing hypotheses or evidence.")
        return

    # Build lookup structures
    cell_lookup = _build_cell_lookup(cells)
    hypothesis_labels = {h["id"]: h["label"] for h in hypotheses}
    hypothesis_ids = [h["id"] for h in hypotheses]
    evidence_map = {e["evidence_id"]: e for e in evidence}
    differentiator_ids = set()
    if tribunal_data:
        for d in tribunal_data.get("differentiators", []):
            differentiator_ids.add(d.get("evidence_id", ""))

    # -------------------------------------------------------------------------
    # TOP HYPOTHESIS HEADER
    # -------------------------------------------------------------------------
    st.markdown("### ACH Analysis")

    header_cols = st.columns([2, 1, 1])
    with header_cols[0]:
        if top_hypothesis:
            label = hypothesis_labels.get(top_hypothesis, top_hypothesis)
            st.metric("Top Hypothesis", f"{top_hypothesis}: {label}")
        else:
            st.metric("Top Hypothesis", "Undetermined")
    with header_cols[1]:
        score_display = f"{top_score:+.1f}" if top_score is not None else "---"
        st.metric("Score", score_display)
    with header_cols[2]:
        st.metric("Evidence Items", f"{evidence_count} / {len(evidence)}")

    # -------------------------------------------------------------------------
    # MATRIX GRID
    # -------------------------------------------------------------------------
    st.markdown("#### Evidence x Hypothesis Grid")

    grid_df, score_matrix = _build_grid_dataframe(
        evidence, hypotheses, hypothesis_ids, cell_lookup, hypothesis_scores,
    )

    styled_df = grid_df.style.map(
        _style_cell, subset=[h["label"] for h in hypotheses],
    )

    st.dataframe(
        styled_df,
        use_container_width=True,
        hide_index=True,
        height=min(40 * (len(evidence) + 2), 650),
    )

    # -------------------------------------------------------------------------
    # MASTER-DETAIL: EVIDENCE DRILL-DOWN
    # -------------------------------------------------------------------------
    st.markdown("#### Evidence Detail")

    evidence_options = [
        f"{e['evidence_id']}: {e['label']}" for e in evidence
    ]
    selected_label = st.selectbox(
        "Select evidence item",
        evidence_options,
        index=0,
        key="ach_evidence_selector",
    )

    if selected_label:
        selected_eid = selected_label.split(":")[0].strip()
        _render_evidence_detail(
            selected_eid,
            evidence_map,
            hypothesis_ids,
            hypothesis_labels,
            cell_lookup,
            differentiator_ids,
        )

    # -------------------------------------------------------------------------
    # NARRATIVE PANEL
    # -------------------------------------------------------------------------
    if tribunal_data:
        _render_narrative_panel(tribunal_data, hypothesis_labels)

    # -------------------------------------------------------------------------
    # EXPORT
    # -------------------------------------------------------------------------
    _render_export_button(grid_df)


# =============================================================================
# GRID CONSTRUCTION
# =============================================================================

def _build_cell_lookup(cells: list[dict]) -> dict[tuple[str, str], Optional[int]]:
    """Build (evidence_id, hypothesis_id) -> score lookup from flat cell list."""
    lookup: dict[tuple[str, str], Optional[int]] = {}
    for cell in cells:
        key = (cell.get("evidence_id", ""), cell.get("hypothesis_id", ""))
        lookup[key] = cell.get("score")
    return lookup


def _build_grid_dataframe(
    evidence: list[dict],
    hypotheses: list[dict],
    hypothesis_ids: list[str],
    cell_lookup: dict[tuple[str, str], Optional[int]],
    hypothesis_scores: dict[str, float],
) -> tuple[pd.DataFrame, dict]:
    """Build the grid DataFrame with evidence rows and a scores summary row.

    Returns:
        Tuple of (DataFrame for display, raw score matrix dict).
    """
    rows = []
    score_matrix: dict[str, dict[str, Optional[int]]] = {}

    for e in evidence:
        eid = e["evidence_id"]
        label = e["label"]
        available = e.get("available", True)
        row_label = f"{eid}: {label}"
        row: dict[str, str] = {"Evidence": row_label}
        score_matrix[eid] = {}

        for h, hid in zip(hypotheses, hypothesis_ids):
            if not available:
                row[h["label"]] = "N/A"
                score_matrix[eid][hid] = None
            else:
                score = cell_lookup.get((eid, hid))
                row[h["label"]] = _SCORE_LABELS.get(score, "N/A")
                score_matrix[eid][hid] = score

        rows.append(row)

    # Summary row with hypothesis totals
    summary_row: dict[str, str] = {"Evidence": "TOTAL SCORE"}
    for h, hid in zip(hypotheses, hypothesis_ids):
        total = hypothesis_scores.get(hid, 0.0)
        summary_row[h["label"]] = f"{total:+.1f}"
    rows.append(summary_row)

    df = pd.DataFrame(rows)
    return df, score_matrix


def _style_cell(val: str) -> str:
    """Return CSS style string for a grid cell value."""
    if not isinstance(val, str):
        return ""

    # Handle total score row
    if val.startswith("+") or val.startswith("-"):
        try:
            numeric = float(val)
            if numeric > 0:
                return _SCORE_TOTAL_POSITIVE
            elif numeric < 0:
                return _SCORE_TOTAL_NEGATIVE
            else:
                return _SCORE_TOTAL_NEUTRAL
        except ValueError:
            pass

    return _SCORE_COLORS.get(val, "")


# =============================================================================
# EVIDENCE DETAIL
# =============================================================================

def _render_evidence_detail(
    evidence_id: str,
    evidence_map: dict[str, dict],
    hypothesis_ids: list[str],
    hypothesis_labels: dict[str, str],
    cell_lookup: dict[tuple[str, str], Optional[int]],
    differentiator_ids: set[str],
) -> None:
    """Render detail panel for a selected evidence item."""
    e = evidence_map.get(evidence_id)
    if not e:
        st.warning(f"Evidence {evidence_id} not found.")
        return

    with st.expander(f"Detail: {e['evidence_id']} -- {e['label']}", expanded=True):
        info_cols = st.columns([1, 1, 1])
        with info_cols[0]:
            st.markdown(f"**Evidence ID:** {e['evidence_id']}")
        with info_cols[1]:
            raw_val = e.get("raw_value")
            if isinstance(raw_val, float):
                st.markdown(f"**Raw Value:** {raw_val:.4f}")
            elif raw_val is not None:
                st.markdown(f"**Raw Value:** {raw_val}")
            else:
                st.markdown("**Raw Value:** N/A")
        with info_cols[2]:
            available = e.get("available", True)
            status_text = "Available" if available else "Not Available"
            st.markdown(f"**Status:** {status_text}")

        if evidence_id in differentiator_ids:
            st.markdown(
                ":orange[**DIFFERENTIATOR** -- This evidence distinguishes "
                "between competing hypotheses.]"
            )

        if not e.get("available", True):
            st.caption("This evidence item was not available for scoring.")
            return

        # Supports / Opposes breakdown
        supports: list[str] = []
        opposes: list[str] = []
        neutral: list[str] = []

        for hid in hypothesis_ids:
            score = cell_lookup.get((evidence_id, hid))
            h_label = f"{hid}: {hypothesis_labels.get(hid, hid)}"
            if score == 1:
                supports.append(h_label)
            elif score == -1:
                opposes.append(h_label)
            elif score == 0:
                neutral.append(h_label)

        breakdown_cols = st.columns(3)
        with breakdown_cols[0]:
            st.markdown("**Supports (CONSISTENT)**")
            if supports:
                for s in supports:
                    st.markdown(f"- :green[{s}]")
            else:
                st.caption("None")
        with breakdown_cols[1]:
            st.markdown("**Opposes (INCONSISTENT)**")
            if opposes:
                for o in opposes:
                    st.markdown(f"- :red[{o}]")
            else:
                st.caption("None")
        with breakdown_cols[2]:
            st.markdown("**Neutral**")
            if neutral:
                for n in neutral:
                    st.markdown(f"- {n}")
            else:
                st.caption("None")


# =============================================================================
# NARRATIVE PANEL
# =============================================================================

def _render_narrative_panel(
    tribunal_data: dict,
    hypothesis_labels: dict[str, str],
) -> None:
    """Render tribunal bull/bear narratives and differentiator list."""
    st.divider()
    st.markdown("#### Tribunal Narrative")

    bull_summary = tribunal_data.get("bull_summary", "")
    bear_summary = tribunal_data.get("bear_summary", "")
    differentiators = tribunal_data.get("differentiators", [])
    diff_count = tribunal_data.get("differentiator_count", len(differentiators))

    narrative_cols = st.columns(2)

    with narrative_cols[0]:
        st.markdown("**Bull Case**")
        if bull_summary:
            formatted_bull = _format_citations(bull_summary)
            st.markdown(formatted_bull)
        else:
            st.caption("No bull case narrative available.")

    with narrative_cols[1]:
        st.markdown("**Bear Case**")
        if bear_summary:
            formatted_bear = _format_citations(bear_summary)
            st.markdown(formatted_bear)
        else:
            st.caption("No bear case narrative available.")

    # Differentiators
    if differentiators:
        st.markdown(f"**Differentiating Evidence** ({diff_count} items)")
        for d in differentiators:
            eid = d.get("evidence_id", "")
            label = d.get("evidence_label", "")
            favors = d.get("favors", [])
            opposes = d.get("opposes", [])

            favors_text = ", ".join(
                f"{fid} ({hypothesis_labels.get(fid, fid)})" for fid in favors
            )
            opposes_text = ", ".join(
                f"{oid} ({hypothesis_labels.get(oid, oid)})" for oid in opposes
            )

            with st.container():
                diff_cols = st.columns([1, 2, 2])
                with diff_cols[0]:
                    st.markdown(f"**{eid}:** {label}")
                with diff_cols[1]:
                    st.markdown(f":green[Favors:] {favors_text}" if favors_text else "")
                with diff_cols[2]:
                    st.markdown(f":red[Opposes:] {opposes_text}" if opposes_text else "")
    elif diff_count == 0:
        st.caption("No differentiating evidence found.")


def _format_citations(text: str) -> str:
    """Format [E{n}] citations as bold markdown references."""
    return re.sub(r'\[(E\d+)\]', r'**[\1]**', text)


# =============================================================================
# CSV EXPORT
# =============================================================================

def _render_export_button(grid_df: pd.DataFrame) -> None:
    """Render a download button for CSV export of the ACH grid."""
    st.divider()

    buffer = io.StringIO()
    grid_df.to_csv(buffer, index=False)
    csv_bytes = buffer.getvalue().encode("utf-8")

    st.download_button(
        label="Export ACH Matrix (CSV)",
        data=csv_bytes,
        file_name="ach_matrix_export.csv",
        mime="text/csv",
        key="ach_export_csv",
    )
