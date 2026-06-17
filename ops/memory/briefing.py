import json
import logging
import difflib
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Set, Optional

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path as _Path

    here = _Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / "requirements.txt").exists() or (parent / ".git").exists():
            sys.path.insert(0, str(parent))
            break

from ops.storage import OpsStorage

logger = logging.getLogger(__name__)

# Environment-configurable defaults
# Lowered from 0.85 to 0.75 to reduce false positive deduplication
_DEFAULT_SIMILARITY_THRESHOLD = 0.75
_DEFAULT_JACCARD_PREFILTER = 0.50
_DEFAULT_TOKEN_BUDGET = 2000


class BriefingGenerator:
    def __init__(
        self,
        db_path: str | None = None,
        output_dir: str = "ops/artifacts",
        similarity_threshold: Optional[float] = None,
    ):
        self.storage = OpsStorage(db_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.json_path = self.output_dir / "briefing.json"
        self.md_path = self.output_dir / "BRIEFING.md"

        # Allow tuning via constructor or env var.
        # Lowered default threshold from 0.85 to 0.75 to avoid merging
        # legitimately different facts (e.g., "20% margin" vs "30% margin")
        self.similarity_threshold = similarity_threshold or float(
            os.environ.get("BRIEFING_SIMILARITY_THRESHOLD", _DEFAULT_SIMILARITY_THRESHOLD)
        )
        self.jaccard_prefilter = float(
            os.environ.get("BRIEFING_JACCARD_PREFILTER", _DEFAULT_JACCARD_PREFILTER)
        )
        self.token_budget = int(
            os.environ.get("BRIEFING_TOKEN_BUDGET", _DEFAULT_TOKEN_BUDGET)
        )

    def fetch_active_facts(self) -> List[Dict]:
        with self.storage.transaction() as conn:
            cursor = conn.execute(
                """
                SELECT id, type, content, confidence
                FROM memory_facts
                WHERE status = 'active'
                AND superseded_by IS NULL
                ORDER BY id DESC
                """
            )

            return [
                {"id": row[0], "type": row[1], "content": row[2], "confidence": row[3]}
                for row in cursor.fetchall()
            ]

    @staticmethod
    def _token_estimate(text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    def _pack_facts_with_budget(self, facts: List[Dict], max_tokens: Optional[int] = None) -> Dict:
        max_tokens = max_tokens or self.token_budget

        TYPE_PRIORITY = {"constraint": 0, "nuance": 1, "example": 2}

        sorted_facts = sorted(
            facts,
            key=lambda f: (
                TYPE_PRIORITY.get(f["type"], 99),
                -float(f.get("confidence") or 0.0),
                -int(f["id"]),
            ),
        )

        packed = {"constraints": [], "nuances": [], "examples": []}

        packed_texts: List[str] = []
        packed_exact: Set[str] = set()
        packed_wordsets: List[Set[str]] = []

        est_tokens = 0

        for fact in sorted_facts:
            content = (fact.get("content") or "").strip()
            if not content:
                continue

            if content in packed_exact:
                continue

            content_words = set(content.lower().split())
            is_duplicate = False

            for existing, existing_words in zip(packed_texts, packed_wordsets):
                if abs(len(content) - len(existing)) > 30:
                    continue

                union = len(content_words | existing_words)
                if union == 0:
                    continue
                intersection = len(content_words & existing_words)
                if (intersection / union) < self.jaccard_prefilter:
                    continue

                similarity = difflib.SequenceMatcher(None, content, existing).ratio()
                if similarity > self.similarity_threshold:
                    is_duplicate = True
                    logger.debug(f"Skipping duplicate: '{content}' (similar to '{existing}')")
                    break

            if is_duplicate:
                continue

            cost = self._token_estimate(content)
            if est_tokens + cost > max_tokens:
                logger.info(f"Budget full ({est_tokens}/{max_tokens}). Stopping.")
                break

            category = f"{fact['type']}s"
            if category in packed:
                formatted = f"• {content} (confidence: {float(fact['confidence'] or 0.0):.0%})"
                packed[category].append(formatted)

                packed_texts.append(content)
                packed_exact.add(content)
                packed_wordsets.append(content_words)
                est_tokens += cost

        logger.info(f"Packed {len(packed_texts)} unique facts using ~{est_tokens} tokens")
        return packed

    def generate_json(self) -> Dict:
        facts = self.fetch_active_facts()
        policy = self._pack_facts_with_budget(facts)

        briefing = {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "constraints_count": len(policy["constraints"]),
                "nuances_count": len(policy["nuances"]),
                "examples_count": len(policy["examples"]),
            },
            "policy": policy,
        }

        temp_path = self.json_path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(briefing, f, indent=2, ensure_ascii=False)

        temp_path.replace(self.json_path)
        logger.info(f"Generated JSON briefing: {self.json_path}")

        return briefing

    def generate_markdown(self, briefing: Dict = None) -> None:
        if briefing is None:
            briefing = self.generate_json()

        md_content = [
            "# Investment Briefing",
            f"*Generated: {datetime.now(timezone.utc).isoformat()}*",
            "",
            f"**Total Constraints:** {briefing['meta']['constraints_count']}",
            f"**Total Nuances:** {briefing['meta']['nuances_count']}",
            f"**Total Examples:** {briefing['meta']['examples_count']}",
            "",
            "---",
            "",
            "## Constraints (Hard Rules)",
            "",
        ]

        if briefing["policy"]["constraints"]:
            md_content.extend(briefing["policy"]["constraints"])
        else:
            md_content.append("*No constraints defined*")

        md_content.extend(["", "## Nuances (Soft Preferences)", ""])

        if briefing["policy"]["nuances"]:
            md_content.extend(briefing["policy"]["nuances"])
        else:
            md_content.append("*No nuances defined*")

        md_content.extend(["", "## Examples (Reference Cases)", ""])

        if briefing["policy"]["examples"]:
            md_content.extend(briefing["policy"]["examples"])
        else:
            md_content.append("*No examples defined*")

        temp_path = self.md_path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            f.write("\n".join(md_content))

        temp_path.replace(self.md_path)
        logger.info(f"Generated markdown briefing: {self.md_path}")

    def run(self) -> None:
        print("📋 Generating briefing...")

        json_briefing = self.generate_json()
        self.generate_markdown(json_briefing)

        print("✅ Briefing generated:")
        print(f"   - JSON: {self.json_path}")
        print(f"   - Markdown: {self.md_path}")
        print(f"   - {json_briefing['meta']['constraints_count']} constraints")
        print(f"   - {json_briefing['meta']['nuances_count']} nuances")
        print(f"   - {json_briefing['meta']['examples_count']} examples")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generator = BriefingGenerator()
    generator.run()
