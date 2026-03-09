from __future__ import annotations
from typing import Dict, List, Any, Tuple
import tiktoken

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class AdaptiveTokenBudgeter:
    """Dynamically allocates tokens across different context components."""
    
    def __init__(self, max_tokens: int = settings.UX_TOKEN_LIMIT):
        self.max_tokens = max_tokens
        # Default to cl100k_base (GPT-4/Turbo)
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.encoder = tiktoken.encoding_for_model("gpt-3.5-turbo")

    def count_tokens(self, text: str) -> int:
        return len(self.encoder.encode(text))

    def calculate_budget(
        self, 
        system_prompt: str, 
        user_query: str, 
        memory_content: str, 
        rag_context: str
    ) -> Dict[str, int]:
        """
        Calculates a proportional budget for each component.
        Priority: System > User > RAG > Memory
        """
        
        # 1. Mandatory fixed costs
        system_tokens = self.count_tokens(system_prompt)
        user_tokens = self.count_tokens(user_query)
        
        remaining = self.max_tokens - (system_tokens + user_tokens + 100) # reserve 100 for safety
        
        if remaining <= 0:
            _LOG.warning("System + User query exceeds total token budget!")
            return {
                "system": system_tokens,
                "user": user_tokens,
                "memory": 0,
                "rag": 0
            }

        # 2. Allocating remaining tokens (60% RAG, 40% Memory)
        rag_budget = int(remaining * 0.6)
        memory_budget = remaining - rag_budget
        
        return {
            "system": system_tokens,
            "user": user_tokens,
            "memory": memory_budget,
            "rag": rag_budget
        }

    def fit_to_budget(self, text: str, budget: int) -> str:
        """Clips text to fit within a specific token budget."""
        if not text or budget <= 0:
            return ""
        
        tokens = self.encoder.encode(text)
        if len(tokens) <= budget:
            return text
            
        _LOG.info(f"Clipping text from {len(tokens)} to {budget} tokens.")
        return self.encoder.decode(tokens[:budget])
