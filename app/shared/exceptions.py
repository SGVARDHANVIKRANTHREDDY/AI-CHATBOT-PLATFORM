class ChatBotError(Exception):
    """Base exception for all ChatBot errors."""

    pass


class LLMProviderError(ChatBotError):
    pass


class RAGError(ChatBotError):
    pass


class MemoryError(ChatBotError):
    pass


class ConfigurationError(ChatBotError):
    pass


class SecurityError(ChatBotError):
    pass
