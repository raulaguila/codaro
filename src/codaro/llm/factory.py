from codaro.llm.openai import OpenAICompatible


def create_provider(settings, *, transport=None):
    if settings.api_style == "ollama":
        from codaro.llm.ollama import Ollama

        return Ollama(settings, transport)
    if settings.api_style == "anthropic":
        from codaro.llm.anthropic import Anthropic

        return Anthropic(settings, transport)
    return OpenAICompatible(settings, transport)
