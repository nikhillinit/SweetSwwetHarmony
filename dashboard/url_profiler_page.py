"""
URL Profiler Page - Paste URL → Structured Profile with Evidence

Dark theme design consistent with Mini-Scout.
"""

import asyncio
import streamlit as st
from datetime import datetime
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def run_async(coro):
    """Helper to run async code in Streamlit."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def inject_profiler_css():
    """Inject custom CSS for URL Profiler page."""
    st.markdown("""
    <style>
    /* Dark theme - Press On Ventures style */
    .stApp {
        background-color: #0a0a0a;
        color: #E1D8D1;
    }

    /* Profile card styling */
    .profile-card {
        border: 1px solid #2a2520;
        border-left: 3px solid #E1D8D1;
        border-radius: 4px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 0.75rem;
        background: #111111;
    }

    .profile-header {
        font-size: 1.5rem;
        font-weight: 500;
        color: #E1D8D1;
        margin-bottom: 0.5rem;
    }

    .profile-domain {
        font-size: 0.9rem;
        color: #7a7267;
        margin-bottom: 1rem;
    }

    /* Field card */
    .field-card {
        border: 1px solid #2a2520;
        border-radius: 4px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        background: #0d0d0d;
    }

    .field-label {
        font-size: 0.7rem;
        font-weight: 600;
        color: #7a7267;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-bottom: 0.25rem;
    }

    .field-value {
        font-size: 1rem;
        color: #E1D8D1;
        margin-bottom: 0.5rem;
    }

    .field-evidence {
        font-size: 0.85rem;
        color: #9CA3AF;
        font-style: italic;
        padding: 0.5rem;
        background: #1a1a1a;
        border-left: 2px solid #3a3530;
        margin-top: 0.5rem;
    }

    /* Confidence badge */
    .confidence-bar {
        height: 4px;
        background: #2a2520;
        border-radius: 2px;
        margin-top: 0.5rem;
    }

    .confidence-fill {
        height: 100%;
        border-radius: 2px;
    }

    .confidence-high {
        background: #10B981;
    }

    .confidence-medium {
        background: #FBBF24;
    }

    .confidence-low {
        background: #F87171;
    }

    /* Category tags */
    .category-tag {
        display: inline-block;
        font-size: 0.7rem;
        padding: 0.2rem 0.6rem;
        border-radius: 2px;
        font-weight: 500;
        margin-right: 0.5rem;
        margin-bottom: 0.25rem;
        background: rgba(225, 216, 209, 0.1);
        color: #E1D8D1;
        border: 1px solid rgba(225, 216, 209, 0.2);
    }

    /* Error message */
    .error-box {
        background: rgba(239, 68, 68, 0.1);
        border: 1px solid rgba(239, 68, 68, 0.3);
        border-radius: 4px;
        padding: 1rem;
        color: #F87171;
    }

    /* Success message */
    .success-box {
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.3);
        border-radius: 4px;
        padding: 1rem;
        color: #34D399;
    }

    /* Similar companies section */
    .similar-section {
        margin-top: 2rem;
        padding-top: 1.5rem;
        border-top: 1px solid #2a2520;
    }

    .similar-card {
        border: 1px solid #2a2520;
        border-radius: 4px;
        padding: 1rem;
        margin-bottom: 0.75rem;
        background: #0d0d0d;
    }

    .similar-card:hover {
        border-color: #E1D8D1;
    }

    .similar-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
    }

    .similar-name {
        font-size: 1.1rem;
        font-weight: 500;
        color: #E1D8D1;
    }

    .similar-score {
        font-size: 0.9rem;
        font-weight: 600;
        padding: 0.2rem 0.6rem;
        border-radius: 2px;
        background: rgba(16, 185, 129, 0.2);
        color: #34D399;
    }

    .similar-reasons {
        font-size: 0.85rem;
        color: #9CA3AF;
        margin-top: 0.5rem;
    }

    .similar-meta {
        font-size: 0.75rem;
        color: #7a7267;
        margin-top: 0.25rem;
    }
    </style>
    """, unsafe_allow_html=True)


def render_confidence_bar(confidence: float) -> str:
    """Render a confidence bar HTML."""
    percentage = int(confidence * 100)

    if confidence >= 0.7:
        color_class = "confidence-high"
    elif confidence >= 0.4:
        color_class = "confidence-medium"
    else:
        color_class = "confidence-low"

    return f"""
    <div class="confidence-bar">
        <div class="confidence-fill {color_class}" style="width: {percentage}%"></div>
    </div>
    <span style="font-size: 0.75rem; color: #7a7267;">{percentage}% confidence</span>
    """


def render_field_card(
    label: str,
    field,
    show_evidence: bool = True,
) -> None:
    """Render a single extracted field as a card."""
    if field is None:
        return

    st.markdown(f"""
    <div class="field-card">
        <div class="field-label">{label}</div>
        <div class="field-value">{field.value}</div>
        {render_confidence_bar(field.confidence)}
    </div>
    """, unsafe_allow_html=True)

    if show_evidence and field.evidence_snippet:
        st.markdown(f"""
        <div class="field-evidence">"{field.evidence_snippet}"</div>
        """, unsafe_allow_html=True)


def render_category_tags(categories: list) -> None:
    """Render category hint tags."""
    if not categories:
        return

    tags_html = "".join([
        f'<span class="category-tag">{cat}</span>'
        for cat in categories
    ])

    st.markdown(f"""
    <div class="field-card">
        <div class="field-label">Categories</div>
        <div style="margin-top: 0.5rem;">{tags_html}</div>
    </div>
    """, unsafe_allow_html=True)


def render_similar_company_card(company) -> None:
    """Render a single similar company card."""
    score_pct = int(company.similarity_score * 100)
    reasons = ", ".join(company.match_reasons[:3])

    st.markdown(f"""
    <div class="similar-card">
        <div class="similar-header">
            <span class="similar-name">{company.company_name or company.canonical_key}</span>
            <span class="similar-score">{score_pct}% match</span>
        </div>
        <div class="similar-reasons">{reasons}</div>
        <div class="similar-meta">{company.category} • {company.business_model}</div>
    </div>
    """, unsafe_allow_html=True)


def render_similar_companies(similar_companies: list) -> None:
    """Render the similar companies section."""
    if not similar_companies:
        st.info("No similar companies found. Try profiling more companies first.")
        return

    st.markdown(f"""
    <div class="similar-section">
        <h3 style="color: #E1D8D1; margin-bottom: 1rem;">Similar Companies ({len(similar_companies)} found)</h3>
    </div>
    """, unsafe_allow_html=True)

    # Display top results
    for company in similar_companies[:10]:
        render_similar_company_card(company)


def find_similar_companies(canonical_key: str, db_path: str = "signals.db"):
    """
    Find similar companies for the given canonical key.

    Args:
        canonical_key: The company's canonical key
        db_path: Path to the database

    Returns:
        List of SimilarCompany objects
    """
    try:
        from storage.embedding_store import EmbeddingStore
        from utils.embedding_generator import EmbeddingGenerator
        from utils.similarity_engine import SimilarityEngine

        async def run_search():
            async with EmbeddingStore(db_path=db_path) as store:
                generator = EmbeddingGenerator()
                engine = SimilarityEngine(
                    embedding_store=store,
                    embedding_generator=generator,
                )
                return await engine.find_similar(canonical_key, n=20)

        return run_async(run_search())

    except Exception as e:
        logger.error(f"Similar companies error: {e}")
        return []


def render_profile_result(profile) -> None:
    """Render the complete profile result."""
    # Header
    company_name = "Unknown Company"
    if profile.extraction_result and profile.extraction_result.company_name:
        company_name = profile.extraction_result.company_name.value

    st.markdown(f"""
    <div class="profile-card">
        <div class="profile-header">{company_name}</div>
        <div class="profile-domain">{profile.domain}</div>
    </div>
    """, unsafe_allow_html=True)

    if not profile.extraction_result:
        st.warning("Could not extract profile information from this URL.")
        return

    result = profile.extraction_result

    # Extracted fields
    col1, col2 = st.columns(2)

    with col1:
        render_field_card("Problem Solved", result.problem_solved)
        render_field_card("Business Model", result.business_model)

    with col2:
        render_field_card("Target Customer", result.target_customer)
        render_field_card("Pricing Model", result.pricing_model)

    # Category tags
    render_category_tags(result.category_hints)

    # Pages fetched info
    with st.expander("Pages Fetched", expanded=False):
        for page in profile.pages_fetched:
            status_icon = "✓" if page.success else "✗"
            status_color = "#10B981" if page.success else "#F87171"
            st.markdown(
                f"<span style='color: {status_color}'>{status_icon}</span> "
                f"**{page.path}** - {page.url}",
                unsafe_allow_html=True
            )

    # Profile metadata
    st.markdown("---")
    st.caption(
        f"Profiled at {profile.last_profiled_at.strftime('%Y-%m-%d %H:%M:%S UTC') if profile.last_profiled_at else 'N/A'} | "
        f"Extraction: {result.extraction_method} | "
        f"Fields: {result.fields_extracted}"
    )


def render_url_profiler_page(store=None):
    """
    Main URL Profiler page.

    Args:
        store: Optional SignalStore for claim storage
    """
    inject_profiler_css()

    st.markdown('<h1 class="main-title">URL Profiler</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="subtitle">Paste a URL to generate a structured company profile with evidence</p>',
        unsafe_allow_html=True
    )

    # URL input
    url = st.text_input(
        "Company URL",
        placeholder="https://example.com",
        help="Enter the company's website URL (homepage is best)"
    )

    # Options
    col1, col2 = st.columns([1, 3])

    with col1:
        force_refresh = st.checkbox("Force refresh", value=False)

    with col2:
        if st.button("Profile", type="primary", disabled=not url):
            if url:
                with st.spinner("Fetching and analyzing website..."):
                    profile = profile_url(url, store, force_refresh)

                    if profile:
                        st.session_state["last_profile"] = profile

    # Display result
    if "last_profile" in st.session_state:
        profile = st.session_state["last_profile"]

        if profile.error:
            st.markdown(f"""
            <div class="error-box">
                <strong>Error:</strong> {profile.error}
            </div>
            """, unsafe_allow_html=True)
        else:
            render_profile_result(profile)

            # Show claims if available
            if profile.claims:
                st.markdown("### Stored Claims")
                for claim in profile.claims:
                    st.markdown(f"- **{claim.claim.predicate}**: {claim.claim.value}")

            # Find Similar Companies button
            st.markdown("---")
            col1, col2 = st.columns([1, 4])

            with col1:
                if st.button("Find Similar", type="secondary"):
                    with st.spinner("Finding similar companies..."):
                        similar = find_similar_companies(profile.canonical_key)
                        st.session_state["similar_companies"] = similar

            # Display similar companies if available
            if "similar_companies" in st.session_state:
                render_similar_companies(st.session_state["similar_companies"])


def profile_url(url: str, store=None, force_refresh: bool = False):
    """
    Profile a URL and return the result.

    Args:
        url: URL to profile
        store: Optional SignalStore
        force_refresh: Force re-fetch even if cached

    Returns:
        CompanyProfile or None
    """
    try:
        from profilers.url_profiler import URLProfiler

        profiler = URLProfiler(signal_store=store)

        async def run_profile():
            async with profiler:
                return await profiler.profile(url, force_refresh=force_refresh)

        return run_async(run_profile())

    except Exception as e:
        logger.error(f"Profile error: {e}")
        from profilers.url_profiler import CompanyProfile, generate_canonical_key, extract_domain
        return CompanyProfile(
            canonical_key=generate_canonical_key(url),
            domain=extract_domain(url),
            error=str(e),
        )


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

def main():
    """Run the URL Profiler page standalone."""
    st.set_page_config(
        page_title="URL Profiler - Discovery Engine",
        page_icon="🔍",
        layout="wide",
    )
    render_url_profiler_page()


if __name__ == "__main__":
    main()
