from pathlib import Path
from typing import Tuple

import pytest


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--flannel-version", nargs="?", type=str, default="amd64",
        choices=["amd64", "arm64", "s390x"],
        help="The version of flannel resource. [amd64/arm64/s390x]"
    )
    parser.addoption(
        "--flannel-resource", nargs="?", type=Path,
        default=Path("flannel-amd64.tar.gz"),
        help="The path to the flannel resource. It can be compiled with "
             "`./build-flannel-resources.sh`, see README.md for more information."
    )


@pytest.fixture()
def flannel_resource(pytestconfig) -> Tuple[str, str]:
    version = pytestconfig.getoption("--flannel-version")
    path = pytestconfig.getoption("--flannel-resource")
    if not path.exists():
        raise FileNotFoundError("Missing resource, please provide via"
                                "--flannel-resource option or at {}".format(path))

    return f"flannel-{version}",  path  # noqa: E999
