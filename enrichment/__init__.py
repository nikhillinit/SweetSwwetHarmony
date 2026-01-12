"""
Enrichment Module for Digital Health Intelligence.

Provides API clients for fetching health-related data from external sources:
- ClinicalTrials.gov: Clinical trial records
- OpenFDA: FDA clearances and approvals
- PubMed: Scientific publications
"""

from __future__ import annotations

from enrichment.clinical_trials import ClinicalTrialsClient
from enrichment.openfda import OpenFDAClient
from enrichment.pubmed import PubMedClient

__all__ = ["ClinicalTrialsClient", "OpenFDAClient", "PubMedClient"]
