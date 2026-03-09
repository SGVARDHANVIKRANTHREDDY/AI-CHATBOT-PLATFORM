from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class GraphStore:
    """
    Manages structured entity-relationship knowledge.
    Saves and loads from disk with deduplication logic.
    """
    
    def __init__(self, storage_path: Path = settings.VECTOR_INDEX_DIR / "knowledge_graph.json"):
        self.path = storage_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entities: Set[str] = set()
        self.relationships: List[Dict[str, str]] = []
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.entities = set(data.get("entities", []))
                    self.relationships = data.get("relationships", [])
                _LOG.info(f"Loaded KG with {len(self.entities)} entities and {len(self.relationships)} links.")
            except Exception as e:
                _LOG.error(f"Failed to load KG: {e}")

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "entities": list(self.entities),
                    "relationships": self.relationships
                }, f, indent=2)
        except Exception as e:
            _LOG.error(f"Failed to save KG: {e}")

    def add_data(
        self,
        entities: List[str],
        relationships: List[Dict[str, str]],
        trust_score: Optional[float] = None,
    ) -> None:
        """Add new knowledge with deduplication.

        Args:
            entities: List of entity strings.
            relationships: List of {subject, relation, object} dicts.
            trust_score: Optional trust score to attach to relationships.
        """
        new_entity_count = 0
        for e in entities:
            e_clean = e.strip().lower()
            if e_clean not in self.entities:
                self.entities.add(e_clean)
                new_entity_count += 1
        
        new_rel_count = 0
        for rel in relationships:
            rel_tuple = (
                rel["subject"].lower(),
                rel["relation"].lower(),
                rel["object"].lower(),
            )
            if not any(
                (
                    r["subject"].lower(),
                    r["relation"].lower(),
                    r["object"].lower(),
                )
                == rel_tuple
                for r in self.relationships
            ):
                enriched = dict(rel)
                if trust_score is not None:
                    enriched["trust_score"] = trust_score
                self.relationships.append(enriched)
                new_rel_count += 1
        
        if new_entity_count > 0 or new_rel_count > 0:
            _LOG.info(
                "KG Updated: +%d entities, +%d relationships.",
                new_entity_count,
                new_rel_count,
            )
            self._save()

    def query(self, entity: str) -> List[Dict[str, str]]:
        """Finds all relationships for a given entity."""
        entity = entity.lower()
        return [r for r in self.relationships if r["subject"].lower() == entity or r["object"].lower() == entity]

    def get_summary_for_context(self, text: str) -> str:
        """Surfaces relevant KG links for a given text."""
        relevant_rels = []
        # Simple keyword matching for entities in text
        for e in self.entities:
            if e in text.lower():
                relevant_rels.extend(self.query(e))
        
        if not relevant_rels:
            return ""
            
        # Deduplicate results from search
        unique_rels = []
        seen = set()
        for r in relevant_rels:
            t = (r["subject"], r["relation"], r["object"])
            if t not in seen:
                unique_rels.append(r)
                seen.add(t)
        
        lines = ["--- STRUCTURED KNOWLEDGE (KG) ---"]
        for r in unique_rels[:10]: # Limit for context
            lines.append(f"{r['subject']} → {r['relation']} → {r['object']}")
        
        return "\n".join(lines)

    def get_trusted_relationships(
        self, min_trust: float = 0.5
    ) -> List[Dict[str, str]]:
        """Return only relationships that meet the minimum trust threshold.

        Args:
            min_trust: Minimum trust_score to include (0.0–1.0).

        Returns:
            Filtered list of relationship dicts.
        """
        return [
            r
            for r in self.relationships
            if r.get("trust_score", 1.0) >= min_trust
        ]
