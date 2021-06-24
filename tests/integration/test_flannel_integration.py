import json
import logging
import os
import re
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
    """Get kubeconfig from kubernetes-master."""
    unit = model.applications["kubernetes-master"].units[0]
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
                {"image": "busybox", "name": "test", "args": ["echo", "\"test\""]}
            ]
        }
    }
    resp = api.create_namespaced_pod(body=pod_manifest, namespace="default")
    # wait for pod not to be in pending
    i = 0
    while resp.status.phase == "Pending" and i < 30:
        i += 1
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
        assert _get_flannel_subnet_ip(unit) in cidr_network

    # create test pod
    resp = await _create_test_pod(ops_test.model)
    assert ip_address(resp.status.pod_ip) in cidr_network, \
        "the new pod does not get the ip address in the cidr network"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test, flannel_resource):
    """Build and deploy Flannel in bundle."""
    flannel_charm = await ops_test.build_charm(".")

    # Work around libjuju not handling local file resources by manually
    # pre-deploying the charm w/ resource via the CLI. See
    # https://github.com/juju/python-libjuju/issues/223
    rc, stdout, stderr = await ops_test.run(
        "juju",
        "deploy",
        "-m", ops_test.model_full_name,
        flannel_charm,
        "--resource", flannel_resource,
    )
    assert rc == 0, f"Failed to deploy with resource: {stderr or stdout}"

    bundle = ops_test.render_bundle(
        "tests/data/bundle.yaml",
        master_charm=flannel_charm,
        series="focal",
        # flannel_resource_name=flannel_resource_name,  # This doesn't work currently
        # flannel_resource=flannel_resource,  # This doesn't work currently
    )
    await ops_test.model.deploy(bundle)

    # This configuration is needed due testing on top of LXD containers.
    # https://bugs.launchpad.net/charm-kubernetes-worker/+bug/1903566
    await ops_test.model.applications["kubernetes-worker"].set_config({
        "kubelet-extra-config": "{protectKernelDefaults: false}"
    })

    await ops_test.model.wait_for_idle(wait_for_active=True, timeout=60 * 60,
                                       idle_period=60)


async def test_status_messages(ops_test):
    """Validate that the status messages are correct."""
    await validate_flannel_cidr_network(ops_test)


async def test_change_cidr_network(ops_test):
    """Test configuration change."""
    flannel = ops_test.model.applications["flannel"]
    await flannel.set_config({"cidr": "10.2.0.0/16"})
    await ops_test.model.wait_for_idle(wait_for_active=True, timeout=20 * 60,
                                       idle_period=60)

    # note (rgildein): There is need to restart kubernetes-worker machine.
    #                  https://bugs.launchpad.net/charm-flannel/+bug/1932551
    k8s_worker = ops_test.model.applications["kubernetes-worker"].units[0]
    rc, stdout, stderr = await ops_test.run(
        "ssh",
        "-i", os.path.expanduser("~/.local/share/juju/ssh/juju_id_rsa"),
        "-o", "StrictHostKeyChecking=no",
        "-q",
        "{}@{}".format("ubuntu", k8s_worker.private_address),
        "sudo reboot now"
    )
    assert rc in [0, 255], (f"Failed to restart kubernetes-worker with "
                            f"resource: {stderr or stdout}")
    await ops_test.model.wait_for_idle(wait_for_active=True, timeout=20 * 60,
                                       idle_period=60)
    await validate_flannel_cidr_network(ops_test)
