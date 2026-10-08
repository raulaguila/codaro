"""Compatibility imports. Integration commands live in cli_commands.extensions."""

from codaro.cli_commands.extensions import add_integration as add_integration
from codaro.cli_commands.extensions import features_app as features_app
from codaro.cli_commands.extensions import integrations_app as integrations_app
from codaro.cli_commands.extensions import list_sessions as list_sessions
from codaro.cli_commands.extensions import new_session as new_session
from codaro.cli_commands.extensions import register as register
from codaro.cli_commands.extensions import remove_integration as remove_integration
from codaro.cli_commands.extensions import reverse_interaction as reverse_interaction
from codaro.cli_commands.extensions import sessions_app as sessions_app
from codaro.cli_commands.extensions import set_feature as set_feature
from codaro.cli_commands.extensions import show_features as show_features
from codaro.cli_commands.extensions import test_integrations as test_integrations
from codaro.cli_commands.extensions import use_session as use_session

__all__ = [
    "features_app",
    "integrations_app",
    "sessions_app",
    "show_features",
    "set_feature",
    "add_integration",
    "remove_integration",
    "test_integrations",
    "list_sessions",
    "new_session",
    "use_session",
    "reverse_interaction",
    "register",
]
