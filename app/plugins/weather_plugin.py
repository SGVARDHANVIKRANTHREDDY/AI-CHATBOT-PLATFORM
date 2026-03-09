from __future__ import annotations

from collections.abc import Callable


async def get_weather(location: str) -> str:
    """Mock weather service for testing plugin architecture."""
    return f"The weather in {location} is currently 22°C and sunny (Simulated)."


def register_tools() -> dict[str, Callable]:
    """Registers the weather tool with the agent system."""
    return {"get_weather": get_weather}
