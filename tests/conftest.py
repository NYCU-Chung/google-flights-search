import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--integration", action="store_true", default=False,
        help="Run integration tests (requires network)"
    )
    parser.addoption(
        "--slow", action="store_true", default=False,
        help="Run slow tests (Playwright Stage 5, ~30-60s each)"
    )


def pytest_collection_modifyitems(config, items):
    run_integration = config.getoption("--integration")
    run_slow = config.getoption("--slow")

    skip_integration = pytest.mark.skip(reason="Pass --integration to run")
    skip_slow = pytest.mark.skip(reason="Pass --slow to run (requires playwright + gf-search-setup)")

    for item in items:
        if "integration" in item.keywords and not run_integration:
            item.add_marker(skip_integration)
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)
