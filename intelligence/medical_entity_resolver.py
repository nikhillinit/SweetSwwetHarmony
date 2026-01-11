"""
MedicalEntityResolver: Medical entity extraction and normalization using SciSpacy.

Provides:
- Medical entity extraction (diseases, treatments, devices)
- UMLS concept linking for terminology normalization
- Company name normalization for entity matching

Gracefully degrades when SciSpacy models are not available.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MedicalEntity:
    """A medical entity extracted from text."""
    text: str  # Original text span
    label: str  # Entity type: DISEASE, TREATMENT, DEVICE, etc.
    cui: Optional[str]  # UMLS Concept Unique Identifier
    confidence: float  # Confidence score


@dataclass
class ResolvedHealthEntity:
    """A resolved health entity with normalized information."""
    entity_id: str  # Unique identifier for the entity
    company_name: str  # Original company name
    normalized_name: str  # Normalized company name
    medical_concepts: List[str] = field(default_factory=list)  # UMLS CUIs found in content
    medical_entities: List[MedicalEntity] = field(default_factory=list)  # Extracted medical entities


class MedicalEntityResolver:
    """
    Resolves and normalizes medical entities using SciSpacy.

    Provides:
    - Medical entity extraction from text
    - UMLS concept linking
    - Company name normalization
    - Entity ID generation
    """

    # Regex pattern for company suffixes to remove
    _COMPANY_SUFFIX_PATTERN = re.compile(
        r'\s+(Inc\.?|LLC\.?|Corp\.?|Corporation|Incorporated|Limited|Ltd\.?)$',
        re.IGNORECASE
    )

    def __init__(self, load_model: bool = True, model_name: str = "en_core_sci_sm"):
        """
        Initialize the medical entity resolver.

        Args:
            load_model: Whether to load the SciSpacy model (False for testing)
            model_name: SciSpacy model to load (default: en_core_sci_sm)
        """
        self.model_name = model_name
        self._nlp = None
        self._model_loaded = False

        if load_model:
            self._load_model()

    def _load_model(self) -> None:
        """
        Load the SciSpacy model with graceful degradation.

        If the model is not available, sets _model_loaded to False
        and extraction will return empty results.
        """
        try:
            import spacy
            self._nlp = spacy.load(self.model_name)
            self._model_loaded = True
        except (ImportError, OSError) as e:
            # Graceful degradation - model not available
            self._model_loaded = False
            self._nlp = None

    def _normalize_company_name(self, name: str) -> str:
        """
        Normalize a company name for matching.

        Removes common suffixes (Inc, LLC, Corp) and lowercases.

        Args:
            name: Company name to normalize

        Returns:
            Normalized company name
        """
        # Remove company suffixes
        normalized = self._COMPANY_SUFFIX_PATTERN.sub('', name)
        # Lowercase and strip whitespace
        normalized = normalized.lower().strip()
        return normalized

    def _generate_entity_id(self, company_name: str) -> str:
        """
        Generate a unique entity ID from a company name.

        Uses the normalized name to ensure consistent IDs for
        variations of the same company name.

        Args:
            company_name: Company name to generate ID for

        Returns:
            Hash-based entity ID
        """
        normalized = self._normalize_company_name(company_name)
        # Create MD5 hash of normalized name (truncated for readability)
        hash_obj = hashlib.md5(normalized.encode('utf-8'))
        return f"health_{hash_obj.hexdigest()[:12]}"

    def extract_entities(self, text: str) -> List[MedicalEntity]:
        """
        Extract medical entities from text using SciSpacy.

        Args:
            text: Text to extract entities from

        Returns:
            List of MedicalEntity objects
        """
        if not self._model_loaded or self._nlp is None:
            return []

        doc = self._nlp(text)
        entities = []

        for ent in doc.ents:
            # Map spacy entity labels to our labels
            label = self._map_entity_label(ent.label_)

            # Try to get UMLS CUI if linker is available
            cui = None
            confidence = 0.8  # Default confidence

            # Check if entity has linked concepts (if linker is loaded)
            if hasattr(ent, '_') and hasattr(ent._, 'kb_ents') and ent._.kb_ents:
                # Get the top concept
                top_concept = ent._.kb_ents[0]
                cui = top_concept[0]  # CUI
                confidence = top_concept[1]  # Confidence score

            entities.append(MedicalEntity(
                text=ent.text,
                label=label,
                cui=cui,
                confidence=confidence
            ))

        return entities

    def _map_entity_label(self, spacy_label: str) -> str:
        """
        Map SciSpacy entity labels to our standard labels.

        Args:
            spacy_label: Original label from SciSpacy

        Returns:
            Standardized label
        """
        label_map = {
            'DISEASE': 'DISEASE',
            'DISORDER': 'DISEASE',
            'CHEMICAL': 'TREATMENT',
            'DRUG': 'TREATMENT',
            'MEDICAL_DEVICE': 'DEVICE',
            'PROCEDURE': 'PROCEDURE',
            'ANATOMY': 'ANATOMY',
            'GENE': 'GENE',
            'ORGANISM': 'ORGANISM',
        }
        return label_map.get(spacy_label.upper(), spacy_label.upper())

    def resolve(
        self,
        content: str,
        company_name: str,
        existing_entity_id: Optional[str] = None
    ) -> ResolvedHealthEntity:
        """
        Resolve health entities from content with company context.

        Args:
            content: Text content to analyze
            company_name: Company name for the entity
            existing_entity_id: Optional existing ID to use

        Returns:
            ResolvedHealthEntity with extracted and normalized data
        """
        # Generate or use existing entity ID
        entity_id = existing_entity_id or self._generate_entity_id(company_name)

        # Normalize company name
        normalized_name = self._normalize_company_name(company_name)

        # Extract medical entities
        medical_entities = self.extract_entities(content)

        # Collect unique CUIs
        medical_concepts = []
        for entity in medical_entities:
            if entity.cui and entity.cui not in medical_concepts:
                medical_concepts.append(entity.cui)

        return ResolvedHealthEntity(
            entity_id=entity_id,
            company_name=company_name,
            normalized_name=normalized_name,
            medical_concepts=medical_concepts,
            medical_entities=medical_entities
        )
