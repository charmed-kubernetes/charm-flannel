import json
import logging
import re
import shlex
from ipaddress import ip_address, ip_network
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
    return json.loads(output.data.get("results", {}).get("kubeconfig", "{}"))


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
                {"image": "busybox", "name": "test", "args": ["echo", '"test"']}
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


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test, setup_resources):
    """Build and deploy Flannel in bundle."""
    log.info("Build Charm...")
    charm = await ops_test.build_charm(".")

    log.info("Build Bundle...")
    charm_resources = {
        rsc.name.replace("-", "_").replace(".tar.gz", ""): rsc
        for rsc in setup_resources
    }
    bundle = ops_test.render_bundle(
        "tests/data/bundle.yaml", charm=charm, series="focal", **charm_resources
    )

    log.info("Deploy Bundle...")
    model = ops_test.model_full_name
    cmd = "juju deploy -m {model} {bundle}".format(model=model, bundle=bundle)
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
        rc, stdout, stderr = await ops_test.juju(
            "run",
            "-m",
            ops_test.model_full_name,
            "--unit",
            k8s_worker.name,
            "sudo su root -c '(sleep 5; reboot) &'",
        )
        assert rc == 0, "Failed to reboot {name} with error: {err}".format(
            name=k8s_worker.name, err=stderr or stdout
        )
        log.info(
            "Rebooting {name}...{err}".format(
                name=k8s_worker.name, err=stderr or stdout
            )
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
