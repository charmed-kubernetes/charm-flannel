import os

import pytest

DIR_PATH = os.path.dirname(__file__)


def pytest_addoption(parser):
    parser.addoption("--flannel-version", nargs="?", type=str, default="amd64",
                     choices=["amd64", "arm64", "s390x"],
                     help="The version of flannel resource. [amd64/arm64/s390x]")
    parser.addoption("--flannel-resource", nargs="?", type=str,
                     default=os.path.join(DIR_PATH, "../../flannel-amd64.tar.gz"),
                     help="The path to the flannel resource. It can be compiled with"
                          "`make flannel-resources`, see README.md for more "
                          "information.")


@pytest.fixture()
def flannel_resource_name(pytestconfig):
    version = pytestconfig.getoption("--flannel-version")
    return f"flannel-{version}"  # noqa: E999


@pytest.fixture()
def flannel_resource(pytestconfig):
    return pytestconfig.getoption("--flannel-resource")
