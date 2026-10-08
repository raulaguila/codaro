"""Compatibility imports. Terminal screens live in codaro.ui."""

from codaro.ui.dialogs import NewConversation as NewConversation
from codaro.ui.dialogs import ProviderManager as ProviderManager
from codaro.ui.dialogs import ScopeFields as ScopeFields

__all__ = ["NewConversation", "ScopeFields", "ProviderManager"]
