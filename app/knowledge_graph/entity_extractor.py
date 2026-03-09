from __future__ import annotations

import json
import re
from typing import Any

from app.llm.base import LLMProvider
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class EntityExtractor:
    """
    Extracts entities and relationships from text using LLM.
    Includes filtering logic to prevent noise (count threshold).
    """

    EXTRACT_PROMPT = """Extract key entities and their relationships from the text below.
Focus on: Persons, Organizations, Concepts, and Technologies.
Output as JSON: {{"entities": ["entity1", "entity2"], "relationships": [{{"subject": "s", "relation": "r", "object": "o"}}]}}

Text: {text}
JSON Output:"""

    def __init__(self, llm: LLMProvider, count_threshold: int = 2):
        self.llm = llm
        self.count_threshold = count_threshold
        # Temporary buffer for count-based filtering
        self.seen_counts: dict[str, int] = {}

    async def extract(self, text: str, *, trust_score: float | None = None) -> dict[str, Any]:
        """Extract entities and filter them based on occurrences.

        Args:
            text: Source text to extract from.
            trust_score: Optional trust score (0.0-1.0) from SourceTrustEvaluator.
                         Lower trust means a higher occurrence threshold is needed
                         before an entity is accepted.
        """
        prompt = self.EXTRACT_PROMPT.format(text=text)
        result_str = await self.llm.ask(prompt, system_prompt="You are a knowledge extraction engine.")

        try:
            match = re.search(r"\{.*\}", result_str, re.DOTALL)
            data = json.loads(match.group(0)) if match else {"entities": [], "relationships": []}

            # Adjust threshold by trust: low-trust sources need more occurrences
            effective_threshold = self.count_threshold
            if trust_score is not None and trust_score < 0.7:
                effective_threshold = int(self.count_threshold * (2.0 - trust_score))

            # Apply count-based filtering
            filtered_entities: list[str] = []
            for entity in data.get("entities", []):
                e_clean = entity.strip().lower()
                self.seen_counts[e_clean] = self.seen_counts.get(e_clean, 0) + 1
                if self.seen_counts[e_clean] >= effective_threshold:
                    filtered_entities.append(entity)

            # Filter relationships: keep those involving at least one accepted entity
            accepted_set = {e.strip().lower() for e in filtered_entities}
            filtered_rels = (
                [
                    r
                    for r in data.get("relationships", [])
                    if r.get("subject", "").strip().lower() in accepted_set
                    or r.get("object", "").strip().lower() in accepted_set
                ]
                if accepted_set
                else data.get("relationships", [])
            )

            return {
                "entities": filtered_entities,
                "relationships": filtered_rels,
                "trust_score": trust_score,
            }
        except Exception as e:
            _LOG.error("Extraction failed: %s", e)
            return {"entities": [], "relationships": [], "trust_score": trust_score}
