"""Intentionally incomplete configuration merge for the implementation benchmark."""


def merge_settings(defaults, overrides):
    defaults.update({key: value for key, value in overrides.items() if value})
    return defaults
