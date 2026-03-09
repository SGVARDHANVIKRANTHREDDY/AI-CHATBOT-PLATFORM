from app.vector_memory.vector_store import VectorMemory
from app.knowledge_graph.graph_store import GraphStore
from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class MemoryRetriever:
    """
    Coordinates retrieval across different memory tiers:
    - Episodic: Past conversation events
    - Semantic: General facts learned
    - Profile: User preferences and identity
    - Knowledge Graph: Structured relationships
    """
    
    def __init__(self):
        self.episodic = VectorMemory("episodic")
        self.semantic = VectorMemory("semantic")
        self.profile = VectorMemory("profile")
        self.kg = GraphStore()

    async def retrieve_context(self, query: str) -> str:
        """Surfaces relevant memories and structured knowledge."""
        if not settings.VECTOR_MEMORY_ENABLED:
            return ""
            
        memories = {
            "episodic": await self.episodic.search(query, top_k=2),
            "semantic": await self.semantic.search(query, top_k=2),
            "profile": await self.profile.search(query, top_k=1),
        }
        
        kg_summary = self.kg.get_summary_for_context(query)
        
        lines = []
        if kg_summary:
            lines.append(kg_summary)

        if memories["profile"]:
            lines.append("--- USER PROFILE MEMORY ---")
            for m in memories["profile"]: lines.append(m["text"])
            
        if memories["semantic"]:
            lines.append("--- LEARNED FACTS ---")
            for m in memories["semantic"]: lines.append(m["text"])
            
        if memories["episodic"]:
            lines.append("--- RELEVANT PAST EVENTS ---")
            for m in memories["episodic"]: lines.append(m["text"])
            
        return "\n".join(lines).strip()

    async def store_turn(self, query: str, answer: str):
        """Persists the current turn to long-term episodic memory."""
        if not settings.VECTOR_MEMORY_ENABLED:
            return
            
        # Store full turn in episodic
        await self.episodic.add(f"User: {query}\nAssistant: {answer}")
        
        # Heuristic for semantic facts (can be improved with LLM filter)
        if "remember" in query.lower() or "my name is" in query.lower():
            await self.profile.add(query)
            _LOG.info("Detected profile update, stored in profile memory.")
