import json
import logging
import re
import shlex
from ipaddress import ip_address, ip_network
from pathlib import Path
from time import sleep

import pytest
from kubernetes import client
from kubernetes.config import load_kube_config_from_dict

log = logging.getLogger(__name__)


def _get_flannel_subnet_ip(unit):
    """Get subnet IP address."""
    subnet = re.findall(r"[0-9]+(?:\.[0-9]+){3}", unit.workload_status_message)[0]
    return ip_address(subnet)


async def _get_kubeconfig(model):
    """Get kubeconfig from kubernetes-control-plane."""
    unit = model.applications["kubernetes-control-plane"].units[0]
    action = await unit.run_action("get-kubeconfig")
    output = await action.wait()  # wait for result
    return json.loads(output.results.get("kubeconfig", "{}"))


async def _create_test_pod(model):
    """Create tests pod and return spec."""
    # load kubernetes config
    kubeconfig = await _get_kubeconfig(model)
    load_kube_config_from_dict(kubeconfig)

    api = client.CoreV1Api()
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "test"},
        "spec": {
            "containers": [
                {
                    "image": "rocks.canonical.com/cdk/busybox:1.32",
                    "name": "test",
                    "args": ["echo", '"test"'],
                }
            ]
        },
    }
    log.info("Creating Test Pod")
    resp = api.create_namespaced_pod(body=pod_manifest, namespace="default")
    # wait for pod not to be in pending
    i = 0
    while resp.status.phase == "Pending" and i < 30:
        i += 1
        log.info("pod pending {s} seconds...".format(s=(i - 1) * 10))
        sleep(10)
        resp = api.read_namespaced_pod("test", namespace="default")

    api.delete_namespaced_pod("test", namespace="default")
    return resp


async def validate_flannel_cidr_network(ops_test):
    """Validate network CIDR assign to Flannel."""
    flannel = ops_test.model.applications["flannel"]
    flannel_config = await flannel.get_config()
    cidr_network = ip_network(flannel_config.get("cidr", {}).get("value"))

    for unit in flannel.units:
        assert unit.workload_status == "active"
        subnet = _get_flannel_subnet_ip(unit)
        log.info("{name} reports subnet {subnet}".format(name=unit.name, subnet=subnet))
        assert subnet in cidr_network

    # create test pod
    resp = await _create_test_pod(ops_test.model)
    assert (
        ip_address(resp.status.pod_ip) in cidr_network
    ), "the new pod does not get the ip address in the cidr network"


def remove_ext(path: Path) -> str:
    suffixes = "".join(path.suffixes)
    return path.name.replace(suffixes, "")


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test, series: str, snap_channel: str):
    """Build and deploy Flannel in bundle."""
    charm = next(Path.cwd().glob("flannel*.charm"), None)
    if not charm:
        log.info("Build Charm...")
        charm = await ops_test.build_charm(".")

    resources = list(Path.cwd().glob("flannel*.tar.gz"))
    if not resources:
        log.info("Build Resources...")
        build_script = Path.cwd() / "build-flannel-resources.sh"
        resources = await ops_test.build_resources(build_script, with_sudo=False)
    expected_resources = {"flannel-amd64", "flannel-arm64", "flannel-s390x"}

    if resources and all(remove_ext(rsc) in expected_resources for rsc in resources):
        resources = {remove_ext(rsc).replace("-", "_"): rsc for rsc in resources}
    else:
        log.info("Failed to build resources, downloading from latest/edge")
        arch_resources = ops_test.arch_specific_resources(charm)
        resources = await ops_test.download_resources(charm, resources=arch_resources)
        resources = {name.replace("-", "_"): rsc for name, rsc in resources.items()}

    assert resources, "Failed to build or download charm resources."

    log.info("Build Bundle...")
    context = dict(charm=charm, series=series, snap_channel=snap_channel, **resources)
    overlays = [
        ops_test.Bundle("kubernetes-core", channel="edge"),
        Path("tests/data/charm.yaml"),
    ]
    bundle, *overlays = await ops_test.async_render_bundles(*overlays, **context)

    log.info("Deploy Bundle...")
    model = ops_test.model_full_name
    cmd = f"juju deploy -m {model} {bundle} " + " ".join(
        f"--overlay={f}" for f in overlays
    )
    rc, stdout, stderr = await ops_test.run(*shlex.split(cmd))
    assert rc == 0, "Bundle deploy failed: {}".format((stderr or stdout).strip())

    await ops_test.model.wait_for_idle(status="active", timeout=60 * 60, idle_period=60)


async def test_status_messages(ops_test):
    """Validate that the status messages are correct."""
    await validate_flannel_cidr_network(ops_test)


async def test_change_cidr_network(ops_test):
    """Test configuration change."""
    flannel = ops_test.model.applications["flannel"]
    await flannel.set_config({"cidr": "10.2.0.0/16"})
    rc, stdout, stderr = await ops_test.juju(
        "run",
        "-m",
        ops_test.model_full_name,
        "--application",
        "flannel",
        "hooks/update-status",
    )
    assert rc == 0, "Failed to run hook with resource: {err}".format(
        err=stderr or stdout
    )

    # note (rgildein): There is need to restart kubernetes-worker machines.
    #                  https://bugs.launchpad.net/charm-flannel/+bug/1932551
    for k8s_worker in ops_test.model.applications["kubernetes-worker"].units:
        action = await k8s_worker.run("nohup sudo reboot &>/dev/null & exit")
        log.info(
            "Rebooting {name}...\n{data}".format(
                name=k8s_worker.name, data=json.dumps(action.data, indent=2)
            )
        )
        result = await action.wait()
        stdout = result.results.get("Stdout") or result.results.get("stdout")
        stderr = result.results.get("Stderr") or result.results.get("stderr")
        assert (
            action.status == "completed"
        ), "Failed to reboot {name} with error: {err}".format(
            name=k8s_worker.name, err=stderr or stdout
        )

    await ops_test.model.wait_for_idle(
        status="active", timeout=10 * 60, idle_period=60, raise_on_error=False
    )

    for k8s_worker in ops_test.model.applications["kubernetes-worker"].units:
        rc, stdout, stderr = await ops_test.juju(
            "run", "-m", ops_test.model_full_name, "--unit", k8s_worker.name, "uptime"
        )
        assert rc == 0, "Failed to fetch uptime @{name} with error: {err}".format(
            name=k8s_worker.name, err=stderr or stdout
        )
        log.info(
            "Rebooting complete {name}: uptime {err}".format(
                name=k8s_worker.name, err=stderr or stdout
            )
        )

    log.info("Stability reached after reboot")
    await validate_flannel_cidr_network(ops_test)
