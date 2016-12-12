import os
import json
from shlex import split
from subprocess import check_output, check_call, CalledProcessError, STDOUT

from charms.reactive import set_state, remove_state, when, when_not, hook
from charms.reactive import when_any
from charms.templating.jinja2 import render
from charmhelpers.core.host import service_start, service_stop, service_restart
from charmhelpers.core.host import service_running
from charmhelpers.core.hookenv import log, status_set, resource_get
from charmhelpers.core.hookenv import config, application_version_set


ETCD_PATH = '/etc/ssl/flannel'
ETCD_KEY_PATH = os.path.join(ETCD_PATH, 'client-key.pem')
ETCD_CERT_PATH = os.path.join(ETCD_PATH, 'client-cert.pem')
ETCD_CA_PATH = os.path.join(ETCD_PATH, 'client-ca.pem')


@when_not('flannel.binaries.installed')
def install_flannel_binaries():
    ''' Unpack the Flannel binaries. '''
    try:
        archive = resource_get('flannel')
    except Exception:
        message = 'Error fetching the flannel resource.'
        log(message)
        status_set('blocked', message)
        return
    if not archive:
        message = 'Missing flannel resource.'
        log(message)
        status_set('blocked', message)
        return
    filesize = os.stat(archive).st_size
    if filesize < 1000000:
        message = 'Incomplete flannel resource'
        log(message)
        status_set('blocked', message)
        return
    status_set('maintenance', 'Unpacking flannel resource.')
    charm_dir = os.getenv('CHARM_DIR')
    unpack_path = os.path.join(charm_dir, 'files', 'flannel')
    os.makedirs(unpack_path, exist_ok=True)
    cmd = ['tar', 'xfz', archive, '-C', unpack_path]
    log(cmd)
    check_call(cmd)
    apps = [
        {'name': 'flanneld', 'path': '/usr/local/bin'},
        {'name': 'etcdctl', 'path': '/usr/local/bin'},
        {'name': 'flannel', 'path': '/opt/cni/bin'},
        {'name': 'bridge', 'path': '/opt/cni/bin'},
        {'name': 'host-local', 'path': '/opt/cni/bin'}
    ]
    for app in apps:
        unpacked = os.path.join(unpack_path, app['name'])
        app_path = os.path.join(app['path'], app['name'])
        install = ['install', '-v', '-D', unpacked, app_path]
        check_call(install)
    set_state('flannel.binaries.installed')


@when('cni.is-worker')
@when_not('flannel.cni.configured')
def configure_cni(cni):
    ''' Set up the flannel cni configuration file. '''
    render('10-flannel.conf', '/etc/cni/net.d/10-flannel.conf', {})
    set_state('flannel.cni.configured')


@when('etcd.tls.available')
@when_not('flannel.etcd.credentials.installed')
def install_etcd_credentials(etcd):
    ''' Install the etcd credential files. '''
    etcd.save_client_credentials(ETCD_KEY_PATH, ETCD_CERT_PATH, ETCD_CA_PATH)
    set_state('flannel.etcd.credentials.installed')


@when('flannel.binaries.installed', 'flannel.etcd.credentials.installed',
      'etcd.available')
@when_not('flannel.service.installed')
def install_flannel_service(etcd):
    ''' Install the flannel service. '''
    status_set('maintenance', 'Installing flannel service.')
    default_interface = None
    cmd = ['route']
    output = check_output(cmd).decode('utf8')
    for line in output.split('\n'):
        if 'default' in line:
            default_interface = line.split(' ')[-1]
            break
    context = {'iface': config('iface') or default_interface,
               'connection_string': etcd.get_connection_string(),
               'cert_path': ETCD_PATH}
    render('flannel.service', '/lib/systemd/system/flannel.service', context)
    set_state('flannel.service.installed')
    remove_state('flannel.service.started')


@when('config.changed.iface')
def reconfigure_flannel_service():
    ''' Handle interface configuration change. '''
    remove_state('flannel.service.installed')


@when('flannel.binaries.installed', 'flannel.etcd.credentials.installed',
      'etcd.available')
@when_not('flannel.network.configured')
def configure_network(etcd):
    ''' Store initial flannel data in etcd. '''
    data = json.dumps({
        'Network': config('cidr'),
        'Backend': {
            'Type': 'vxlan'
        }
    })
    cmd = "etcdctl "
    cmd += "--endpoint '{0}' ".format(etcd.get_connection_string())
    cmd += "--cert-file {0} ".format(ETCD_CERT_PATH)
    cmd += "--key-file {0} ".format(ETCD_KEY_PATH)
    cmd += "--ca-file {0} ".format(ETCD_CA_PATH)
    cmd += "set /coreos.com/network/config '{0}'".format(data)
    check_call(split(cmd))
    set_state('flannel.network.configured')
    remove_state('flannel.service.started')


@when('config.changed.cidr')
def reconfigure_network():
    ''' Trigger the network configuration method. '''
    remove_state('flannel.network.configured')


@when('flannel.binaries.installed', 'flannel.service.installed',
      'flannel.network.configured')
@when_not('flannel.service.started')
def start_flannel_service():
    ''' Start the flannel service. '''
    status_set('maintenance', 'Starting flannel service.')
    if service_running('flannel'):
        service_restart('flannel')
    else:
        service_start('flannel')
    set_state('flannel.service.started')


@when('flannel.service.started', 'flannel.cni.configured')
@when_not('flannel.cni.available')
def set_available(cni):
    ''' Indicate to the CNI provider that we're ready. '''
    cni.set_available()
    set_state('flannel.cni.available')


@when('flannel.binaries.installed')
@when_not('flannel.version.set')
def set_flannel_version():
    ''' Surface the currently deployed version of flannel to Juju '''
    cmd = 'flanneld -version'
    version = check_output(split(cmd), stderr=STDOUT).decode('utf-8')
    if version:
        application_version_set(version.split('v')[-1].strip())
        set_state('flannel.version.set')


@when('flannel.service.started')
@when_any('cni.is-master', 'flannel.cni.available')
def ready():
    ''' Indicate that flannel is active. '''
    status_set('active', 'Flannel subnet ' + get_flannel_subnet())


@when_not('etcd.connected')
def halt_execution():
    ''' send a clear message to the user that we are waiting on etcd '''
    status_set('blocked', 'Waiting for etcd relation.')


@hook('upgrade-charm')
def reset_states_and_redeploy():
    ''' Remove state and redeploy '''
    remove_state('flannel.binaries.installed')
    remove_state('flannel.service.started')
    remove_state('flannel.version.set')
    remove_state('flannel.network.configured')
    remove_state('flannel.service.installed')


@hook('stop')
def cleanup_deployment():
    ''' Terminate services, and remove the deployed bins '''
    service_stop('flannel')
    down = 'ip link set flannel.1 down'
    delete = 'ip link delete flannel.1'
    try:
        check_call(split(down))
        check_call(split(delete))
    except CalledProcessError:
        log('Unable to remove iface flannel.1')
        log('Potential indication that cleanup is not possible')
    files = ['/usr/local/bin/flanneld',
             '/lib/systemd/system/flannel',
             '/lib/systemd/system/flannel.service',
             '/var/run/flannel/subnet.env',
             '/usr/local/bin/flanneld',
             '/usr/local/bin/etcdctl',
             '/opt/cni/bin/flannel',
             '/opt/cni/bin/bridge',
             '/opt/cni/bin/host-local',
             ETCD_KEY_PATH,
             ETCD_CERT_PATH,
             ETCD_CA_PATH]
    for f in files:
        if os.path.exists(f):
            log('Removing {}'.format(f))
            os.remove(f)


def get_flannel_subnet():
    ''' Returns the flannel subnet reserved for this unit '''
    with open('/run/flannel/subnet.env') as f:
        raw_data = dict(line.strip().split('=') for line in f)
    return raw_data['FLANNEL_SUBNET']
