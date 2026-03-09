from __future__ import annotations
import faiss
import numpy as np
from typing import List, Dict, Any, Optional
from app.vector_memory.embeddings import embedding_service
from app.tools.registry import TOOL_REGISTRY
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class NeuralToolRouter:
    """
    Selects the best tool for a given query based on semantic similarity 
    between the query and tool descriptions.
    """
    
    def __init__(self, registry: Dict[str, Any] = TOOL_REGISTRY):
        self.registry = registry
        self.tool_names = list(registry.keys())
        self.index: Optional[faiss.Index] = None
        self._initialize_index()

    def _initialize_index(self):
        """Builds a FAISS index of tool descriptions."""
        _LOG.info("Initializing neural tool router index.")
        descriptions = []
        for name in self.tool_names:
            func = self.registry[name]
            doc = func.__doc__ or f"Tool for {name}"
            descriptions.append(f"Tool: {name}. Description: {doc}")
            
        if not descriptions:
            return
            
        embs = embedding_service.encode(descriptions)
        self.index = faiss.IndexFlatIP(embs.shape[1])
        self.index.add(embs)
        _LOG.info(f"Indexed {len(descriptions)} tools for neural routing.")

    async def recommend_tool(self, query: str, threshold: float = 0.3) -> Optional[str]:
        """
        Returns the name of the tool most similar to the query.
        If similarity is below threshold, returns None.
        """
        if self.index is None:
            return None
            
        q_emb = embedding_service.encode_single(query)
        q_emb_np = np.asarray([q_emb], dtype="float32")
        
        scores, idxs = self.index.search(q_emb_np, 1)
        score = scores[0][0]
        idx = idxs[0][0]
        
        if idx != -1 and score >= threshold:
            recommended = self.tool_names[idx]
            _LOG.info(f"Neural router recommended tool: {recommended} (score: {score:.3f})")
            return recommended
            
        return None

    async def get_top_tools(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        """Returns top K tools with their similarity scores."""
        if self.index is None:
            return []
            
        q_emb = embedding_service.encode_single(query)
        q_emb_np = np.asarray([q_emb], dtype="float32")
        
        k = min(len(self.tool_names), k)
        scores, idxs = self.index.search(q_emb_np, k)
        
        results = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx == -1: continue
            results.append({
                "name": self.tool_names[idx],
                "score": float(score),
                "description": self.registry[self.tool_names[idx]].__doc__
            })
        return results
