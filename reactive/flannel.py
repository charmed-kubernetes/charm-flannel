import os
import json
import shutil
from shlex import split
from subprocess import check_output, check_call, CalledProcessError, STDOUT

from charms.flannel.common import retry

from charms.reactive import set_state, remove_state, when, when_not, hook
from charms.reactive import when_any
from charmhelpers.core.templating import render
from charmhelpers.core.host import service_start, service_stop, service_restart
from charmhelpers.core.host import service_running, service
from charmhelpers.core.hookenv import log, resource_get
from charmhelpers.core.hookenv import config, application_version_set
from charmhelpers.core.hookenv import network_get
from charmhelpers.contrib.charmsupport import nrpe
from charms.reactive.helpers import data_changed

from charms.layer import status


ETCD_PATH = "/etc/ssl/flannel"
ETCD_KEY_PATH = os.path.join(ETCD_PATH, "client-key.pem")
ETCD_CERT_PATH = os.path.join(ETCD_PATH, "client-cert.pem")
ETCD_CA_PATH = os.path.join(ETCD_PATH, "client-ca.pem")


@when_not("flannel.binaries.installed")
def install_flannel_binaries():
    """Unpack the Flannel binaries."""
    try:
        resource_name = "flannel-{}".format(arch())
        archive = resource_get(resource_name)
    except Exception:
        message = "Error fetching the flannel resource."
        log(message)
        status.blocked(message)
        return
    if not archive:
        message = "Missing flannel resource."
        log(message)
        status.blocked(message)
        return
    filesize = os.stat(archive).st_size
    if filesize < 1000000:
        message = "Incomplete flannel resource"
        log(message)
        status.blocked(message)
        return
    status.maintenance("Unpacking flannel resource.")
    charm_dir = os.getenv("CHARM_DIR")
    unpack_path = os.path.join(charm_dir, "files", "flannel")
    os.makedirs(unpack_path, exist_ok=True)
    cmd = ["tar", "xfz", archive, "-C", unpack_path]
    log(cmd)
    check_call(cmd)
    apps = [
        {"name": "flanneld", "path": "/usr/local/bin"},
        {"name": "etcdctl", "path": "/usr/local/bin"},
    ]
    for app in apps:
        unpacked = os.path.join(unpack_path, app["name"])
        app_path = os.path.join(app["path"], app["name"])
        install = ["install", "-v", "-D", unpacked, app_path]
        check_call(install)
    os.makedirs("/opt/cni/bin", exist_ok=True)
    shutil.copy(unpack_path + "/cni-plugin/flannel", "/opt/cni/bin")
    set_state("flannel.binaries.installed")


@when("cni.connected")
@when_not("flannel.cni.configured")
def configure_cni(cni):
    """Set up the flannel cni configuration file."""
    render("10-flannel.conflist", "/etc/cni/net.d/10-flannel.conflist", {})
    set_state("flannel.cni.configured")


@when("etcd.tls.available")
@when_not("flannel.etcd.credentials.installed")
def install_etcd_credentials(etcd):
    """Install the etcd credential files."""
    etcd.save_client_credentials(ETCD_KEY_PATH, ETCD_CERT_PATH, ETCD_CA_PATH)
    set_state("flannel.etcd.credentials.installed")


def default_route_interface():
    """Returns the network interface of the system's default route"""
    default_interface = None
    cmd = ["route"]
    output = check_output(cmd).decode("utf8")
    for line in output.split("\n"):
        if "default" in line:
            default_interface = line.split(" ")[-1]
            return default_interface


def get_bind_address_interface():
    """Returns a non-fan bind-address interface for the cni endpoint.
    Falls back to default_route_interface() if bind-address is not available.
    """
    try:
        data = network_get("cni")
    except NotImplementedError:
        # Juju < 2.1
        return default_route_interface()

    if "bind-addresses" not in data:
        # Juju < 2.3
        return default_route_interface()

    for bind_address in data["bind-addresses"]:
        if bind_address["interfacename"].startswith("fan-"):
            continue
        return bind_address["interfacename"]

    # If we made it here, we didn't find a non-fan CNI bind-address, which is
    # unexpected. Let's log a message and play it safe.
    log("Could not find a non-fan bind-address. Using fallback interface.")
    return default_route_interface()


@when(
    "flannel.binaries.installed",
    "flannel.etcd.credentials.installed",
    "etcd.tls.available",
)
@when_not("flannel.service.installed")
def install_flannel_service(etcd):
    """Install the flannel service."""
    status.maintenance("Installing flannel service.")
    # keep track of our etcd conn string and cert info so we can detect when it
    # changes later
    data_changed("flannel_etcd_connections", etcd.get_connection_string())
    data_changed("flannel_etcd_client_cert", etcd.get_client_credentials())
    iface = config("iface") or get_bind_address_interface()
    context = {
        "iface": iface,
        "connection_string": etcd.get_connection_string(),
        "cert_path": ETCD_PATH,
    }
    render("flannel.service", "/lib/systemd/system/flannel.service", context)
    service("enable", "flannel")
    set_state("flannel.service.installed")
    remove_state("flannel.service.started")


@when("config.changed.iface")
def reconfigure_flannel_service():
    """Handle interface configuration change."""
    remove_state("flannel.service.installed")


@when("etcd.available", "flannel.service.installed")
def etcd_changed(etcd):
    if data_changed("flannel_etcd_connections", etcd.get_connection_string()):
        remove_state("flannel.service.installed")
    if data_changed("flannel_etcd_client_cert", etcd.get_client_credentials()):
        etcd.save_client_credentials(ETCD_KEY_PATH, ETCD_CERT_PATH, ETCD_CA_PATH)
        remove_state("flannel.service.installed")


@when(
    "flannel.binaries.installed", "flannel.etcd.credentials.installed", "etcd.available"
)
@when_not("flannel.network.configured")
def invoke_configure_network(etcd):
    """invoke network configuration and adjust states"""
    status.maintenance("Negotiating flannel network subnet.")
    if configure_network(etcd):
        set_state("flannel.network.configured")
        remove_state("flannel.service.started")
    else:
        status.waiting("Waiting on etcd.")


@retry(times=3, delay_secs=20)
def configure_network(etcd):
    """Store initial flannel data in etcd.

    Returns True if the operation completed successfully.

    """
    flannel_config = {"Network": config("cidr"), "Backend": {"Type": "vxlan"}}

    vni = config("vni")
    if vni:
        flannel_config["Backend"]["VNI"] = vni

    port = config("port")
    if port:
        flannel_config["Backend"]["Port"] = port

    data = json.dumps(flannel_config)

    cmd = "etcdctl "
    cmd += "--endpoints '{0}' ".format(etcd.get_connection_string())
    cmd += "--cert {0} ".format(ETCD_CERT_PATH)
    cmd += "--key {0} ".format(ETCD_KEY_PATH)
    cmd += "--cacert {0} ".format(ETCD_CA_PATH)
    cmd += "put /coreos.com/network/config '{0}'".format(data)
    env = dict(os.environ, ETCDCTL_API="3")
    try:
        check_call(split(cmd), env=env)
        return True

    except CalledProcessError:
        log(
            "Unexpected error configuring network. Assuming etcd not"
            " ready. Will retry in 20s"
        )
        return False


@when_any("config.changed.cidr", "config.changed.port", "config.changed.vni")
def reconfigure_network():
    """Trigger the network configuration method."""
    remove_state("flannel.network.configured")


@when(
    "flannel.binaries.installed",
    "flannel.service.installed",
    "flannel.network.configured",
)
@when_not("flannel.service.started")
def start_flannel_service():
    """Start the flannel service."""
    status.maintenance("Starting flannel service.")
    if service_running("flannel"):
        service_restart("flannel")
    else:
        service_start("flannel")
    set_state("flannel.service.started")


@when("cni.connected", "flannel.service.started", "flannel.cni.configured")
@when_not("flannel.cni.available")
def set_available(cni):
    """Indicate to the CNI provider that we're ready."""
    cni.set_config(cidr=config("cidr"), cni_conf_file="10-flannel.conflist")
    set_state("flannel.cni.available")


@when("flannel.binaries.installed")
@when_not("flannel.version.set")
def set_flannel_version():
    """Surface the currently deployed version of flannel to Juju"""
    cmd = "flanneld -version"
    version = check_output(split(cmd), stderr=STDOUT).decode("utf-8")
    if version:
        application_version_set(version.split("v")[-1].strip())
        set_state("flannel.version.set")


NRPE_EXTERNAL = "nrpe-external-master"  # wokeignore:rule=master


@when(NRPE_EXTERNAL + ".available")
@when_not(NRPE_EXTERNAL + ".initial-config")
def initial_nrpe_config(nagios=None):
    set_state(NRPE_EXTERNAL + ".initial-config")
    update_nrpe_config(nagios)


@when("flannel.service.started")
@when(NRPE_EXTERNAL + ".available")
@when_any("config.changed.nagios_context", "config.changed.nagios_servicegroups")
def update_nrpe_config(unused=None):
    # List of systemd services that will be checked
    services = ("flannel",)

    # The current nrpe-external interface doesn't handle a lot of logic,
    # use the charm-helpers code for now.
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname, primary=False)
    nrpe.add_init_service_checks(nrpe_setup, services, current_unit)
    nrpe_setup.write()


@when("flannel.service.started")
@when("flannel.cni.available")
def ready():
    """Indicate that flannel is active."""
    try:
        status.active("Flannel subnet " + get_flannel_subnet())
    except FlannelSubnetNotFound:
        status.waiting("Waiting for Flannel")


@when_not("etcd.connected")
def halt_execution():
    """send a clear message to the user that we are waiting on etcd"""
    status.blocked("Waiting for etcd relation.")


@hook("upgrade-charm")
def reset_states_and_redeploy():
    """Remove state and redeploy"""
    remove_state("flannel.cni.available")
    remove_state("flannel.binaries.installed")
    remove_state("flannel.service.started")
    remove_state("flannel.version.set")
    remove_state("flannel.network.configured")
    remove_state("flannel.service.installed")
    remove_state("flannel.cni.configured")
    try:
        log("Deleting /etc/cni/net.d/10-flannel.conf")
        os.remove("/etc/cni/net.d/10-flannel.conf")
    except FileNotFoundError as e:
        log(str(e))


@hook("pre-series-upgrade")
def pre_series_upgrade():
    status.blocked("Series upgrade in progress")


@hook("stop")
def cleanup_deployment():
    """Terminate services, and remove the deployed bins"""
    service_stop("flannel")
    down = "ip link set flannel.1 down"
    delete = "ip link delete flannel.1"
    try:
        check_call(split(down))
        check_call(split(delete))
    except CalledProcessError:
        log("Unable to remove iface flannel.1")
        log("Potential indication that cleanup is not possible")
    files = [
        "/usr/local/bin/flanneld",
        "/lib/systemd/system/flannel",
        "/lib/systemd/system/flannel.service",
        "/run/flannel/subnet.env",
        "/usr/local/bin/flanneld",
        "/usr/local/bin/etcdctl",
        "/etc/cni/net.d/10-flannel.conflist",
        ETCD_KEY_PATH,
        ETCD_CERT_PATH,
        ETCD_CA_PATH,
    ]
    for f in files:
        if os.path.exists(f):
            log("Removing {}".format(f))
            os.remove(f)


def get_flannel_subnet():
    """Returns the flannel subnet reserved for this unit"""
    try:
        with open("/run/flannel/subnet.env") as f:
            raw_data = dict(line.strip().split("=") for line in f)
        return raw_data["FLANNEL_SUBNET"]
    except FileNotFoundError as e:
        raise FlannelSubnetNotFound() from e


def arch():
    """Return the package architecture as a string."""
    # Get the package architecture for this system.
    architecture = check_output(["dpkg", "--print-architecture"]).rstrip()
    # Convert the binary result into a string.
    architecture = architecture.decode("utf-8")
    return architecture


class FlannelSubnetNotFound(Exception):
    pass
