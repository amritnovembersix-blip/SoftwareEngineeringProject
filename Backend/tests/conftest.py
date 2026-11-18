"""Every data endpoint needs a login. Most tests are about what an endpoint returns, not about
login, so by default they run as a logged-in admin (who may see everything).

Tests about login and access itself opt out with `pytestmark = pytest.mark.real_auth` and send
real tokens (tests/test_auth.py, tests/test_access.py).
"""
import pytest

from app.auth import CurrentUser, current_user
from app.main import app

TEST_ADMIN = CurrentUser(id=0, email="admin@sass.test", role="admin", teacher_id=None,
                         display_name="Test admin")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: send real login tokens instead of the default test admin")


@pytest.fixture(autouse=True)
def _logged_in_as_admin(request):
    if request.node.get_closest_marker("real_auth") or request.module.__name__.endswith("test_auth"):
        yield
        return
    app.dependency_overrides[current_user] = lambda: TEST_ADMIN
    yield
    app.dependency_overrides.pop(current_user, None)
