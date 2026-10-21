def pytest_addoption(parser):
    group = parser.getgroup("codaro live providers")
    group.addoption(
        "--live-provider",
        action="append",
        default=[],
        metavar="PROFILE",
        help="Enable paid/network model tests for a saved BYOK profile (or env); repeatable.",
    )
    group.addoption(
        "--live-execution",
        action="store_true",
        help="Also allow implementation benchmarks in disposable fixture copies.",
    )


def pytest_generate_tests(metafunc):
    if "live_profile" in metafunc.fixturenames:
        import pytest

        profiles = metafunc.config.getoption("--live-provider")
        metafunc.parametrize(
            "live_profile",
            profiles
            or [pytest.param(None, marks=pytest.mark.skip(reason="Use --live-provider PROFILE"))],
            scope="session",
        )
