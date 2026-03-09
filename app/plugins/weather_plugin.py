from __future__ import annotations
from typing import Dict, Any, Callable

async def get_weather(location: str) -> str:
    """Mock weather service for testing plugin architecture."""
    return f"The weather in {location} is currently 22°C and sunny (Simulated)."

def register_tools() -> Dict[str, Callable]:
    """Registers the weather tool with the agent system."""
    return {
        "get_weather": get_weather
    }
