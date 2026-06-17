from __future__ import annotations

import json
from pathlib import Path


class StaleCassetteError(RuntimeError):
    pass


class CassettePolicy:
    """Enforce the vcrpy cassette lifecycle policy.

    Rules:
    - Cassettes live under tests/cassettes/ (checked into git, <=500KB each)
    - A .meta.json sidecar records the fingerprint at recording time
    - assert_fresh() raises StaleCassetteError if fingerprint changed
    - Cassettes must NOT inject synthetic fields — raw API responses are preserved
      so api_shape_changed detection fires on replay if the schema changes
    - Regeneration: delete cassette and re-run with HARMONIC_RECORD_CASSETTES=1
    """

    def __init__(
        self,
        cassette_path: Path,
        fingerprint_path: Path,
        meta_path: Path | None = None,
    ) -> None:
        self.cassette_path = Path(cassette_path)
        self.fingerprint_path = Path(fingerprint_path)
        self.meta_path = meta_path or self.cassette_path.with_suffix(
            self.cassette_path.suffix + ".meta.json"
        )

    def assert_fresh(self) -> None:
        if not self.fingerprint_path.exists():
            return
        current_fp = self.fingerprint_path.read_text().strip()
        if not self.meta_path.exists():
            return
        meta = json.loads(self.meta_path.read_text())
        recorded_fp = meta.get("fingerprint", "")
        if recorded_fp and recorded_fp != current_fp:
            raise StaleCassetteError(
                f"stale cassette {self.cassette_path.name}: "
                f"recorded_fingerprint={recorded_fp!r} != current={current_fp!r}. "
                f"Delete {self.cassette_path} and re-run with HARMONIC_RECORD_CASSETTES=1"
            )

    def assert_no_synthetic_field_injection(self) -> None:
        """Cassettes must preserve raw API responses so api_shape_changed fires on replay."""
        if not self.cassette_path.exists():
            return
        content = self.cassette_path.read_text()
        for marker in ("__synthetic__", "__injected__", "__default__"):
            if marker in content:
                raise StaleCassetteError(
                    f"cassette {self.cassette_path.name} contains synthetic field "
                    f"marker {marker!r} — this would hide api_shape_changed events"
                )
