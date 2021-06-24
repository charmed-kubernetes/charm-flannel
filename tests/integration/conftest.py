import os

import pytest

DIR_PATH = os.path.dirname(__file__)


@pytest.fixture()
def flannel_resource():
    version = "amd64"
    path = os.path.join(DIR_PATH, "../../flannel-amd64.tar.gz")
    return f"flannel-{version}={path}"  # noqa: E999
