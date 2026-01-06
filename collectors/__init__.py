"""
Signal collectors for Discovery Engine.

Each collector:
- Monitors a specific signal source (GitHub, Companies House, etc.)
- Finds early indicators of startup activity
- Returns signals compatible with verification_gate_v2
- Builds canonical keys for deduplication
"""

__version__ = "0.1.0"
