import asyncio
import logging
from urllib.request import urlretrieve
from pathlib import Path
import shlex

import pytest

log = logging.getLogger(__name__)


CNI_ARCH_URL = "https://api.jujucharms.com/charmstore/v5/~containers/flannel-{charm}/resource/flannel-{arch}"  # noqa
CHUNK_SIZE = 16000


async def _retrieve_url(charm, arch, target_file):
    url = CNI_ARCH_URL.format(
        charm=charm,
        arch=arch,
    )
    urlretrieve(url, target_file)


@pytest.fixture()
async def setup_resources(ops_test, tmpdir):
    """Provides the flannel resources needed to deploy the charm."""
    cwd = Path.cwd()
    current_resources = list(cwd.glob("*.tar.gz"))
    if not current_resources:
        # If they are not locally available, try to build them
        log.info("Build Resources...")
        build_script = cwd / "build-flannel-resources.sh"
        rc, stdout, stderr = await ops_test.run(
            *shlex.split("sudo " + build_script), cwd=tmpdir, check=False
        )
        if rc != 0:
            err = (stderr or stdout).strip()
            log.warning("build-flannel-resources failed: {}".format(err))
        current_resources = list(Path(tmpdir).glob("*.tar.gz"))
    if not current_resources:
        # if we couldn't build them, just download a fixed version
        log.info("Downloading Resources...")
        await asyncio.gather(
            *(
                _retrieve_url(619, arch, tmpdir / "flannel-{}.tar.gz".format(arch))
                for arch in ("amd64", "arm64", "s390x")
            )
        )
        current_resources = list(Path(tmpdir).glob("*.tar.gz"))

    yield current_resources
