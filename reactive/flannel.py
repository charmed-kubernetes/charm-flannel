from charms.reactive import hook
from charms.reactive import is_state
from charms.reactive import set_state
from charms.reactive import remove_state
from charms.reactive import when
from charms.reactive import when_not

from charms.templating.jinja2 import render
from charmhelpers.core import host
from charmhelpers.core import hookenv

from shlex import split

from subprocess import Popen
from subprocess import PIPE
from subprocess import STDOUT

import etcd
import json
import os
import subprocess


def _get_default_interface():
    '''Find the default network interface for this host.'''
    cmd = ['route']
    # The route command lists the default interfaces.
    # Destination    Gateway        Genmask      Flags Metric Ref    Use Iface
    # default        10.128.0.1     0.0.0.0      UG    0      0        0 ens4
    output = subprocess.check_output(cmd).decode('utf8')
    # Parse each onen of the lines.
    for line in output.split('\n'):
        # When the line contains 'default'.
        if 'default' in line:
            # The last column is the network interface.
            return line.split(' ')[-1]


def _unpack_resource(tarball, output_dir):
    ''' unpack an arbitrary tarball to an arbitrary path '''
    # Ensure the output directory exists
    os.makedirs(output_dir, exist_ok=True)

    command = 'tar -xzf {0} -C {1}'.format(tarball, output_dir)
    subprocess.check_call(split(command))


def _install_files(file_path, output_path):
    ''' Install files using the unix 'install' command '''
    command = 'install -v {0} {1}'.format(file_path, output_path)
    subprocess.check_call(split(command))


def _build_context(reldata):
    ''' Assemble the context dict for use when rendering files'''
    context = {}
    # Assume the TLS credentials have been placed
    if is_state('etcd.tls.available'):
        cert_path = '/etc/ssl/flannel'
        reldata.save_client_credentials('{}/client-key.pem'.format(cert_path),
                                        '{}/client-cert.pem'.format(cert_path),
                                        '{}/client-ca.pem'.format(cert_path))
    else:
        cert_path = None
    context.update(hookenv.config())
    if not hookenv.config('iface'):
        context.update({'iface': _get_default_interface()})
    context.update({'connection_string': reldata.get_connection_string(),
                    'cert_path': cert_path})

    return context


def _initialize_etcd_with_data(reldata):
    ''' Before flannel can start up, we need to seed etcd with with configured
    CIDR. '''

    connection_string = reldata.get_connection_string()

    # Rely on etcd data replication. We only need the first host
    # out of the HA connection string (if applicable)
    hosts = connection_string.split(',')
    host = hosts[0].split(':')[1].lstrip('//')
    port = hosts[0].split(':')[2]

    tls_required = is_state('etcd.tls.available')
    if tls_required:
        cert_path = '/etc/ssl/flannel'
        key = '{}/client-key.pem'.format(cert_path)
        cert = '{}/client-cert.pem'.format(cert_path)
        ca = '{}/client-ca.pem'.format(cert_path)
        client = etcd.Client(host=host, port=int(port), protocol='https',
                             cert=(cert, key), ca_cert=ca)
    else:
        client = etcd.Client(host=host, port=int(port), protocol='http')

    data = {'Network': hookenv.config('cidr'), 'Backend': {'Type': 'vxlan'}}
    json_data = json.dumps(data)
    client.write('/coreos.com/network/config', json_data)


def _ingest_network_config():
    ''' When flannel configures itself on first boot, it generates an
    environment file (subnet.env).

    return a tuple of subnet, and interface mtu'''

    if not os.path.isfile('/var/run/flannel/subnet.env'):
        # The host has not fully started, and we have no file.
        hookenv.log('Did not find expected file: /var/run/flannel/subnet.env')
        return

    with open('/var/run/flannel/subnet.env') as f:
        flannel_config = f.readlines()

    for f in flannel_config:
        if 'FLANNEL_SUBNET' in f:
            value = f.split('=')[-1].strip()
            subnet = value
        if 'FLANNEL_MTU' in f:
            value = f.split('=')[1].strip()
            mtu = value
    return (subnet, mtu)


@when_not('etcd.connected')
def halt_execution():
    ''' send a clear message to the user that we are waiting on etcd '''
    hookenv.status_set('waiting', 'Waiting for etcd relation.')


@when('etcd.available')
@when_not('flannel.etcd.credentials.placed')
def place_etcd_tls_credentials(etcd):
    ''' TLS terminated etcd instances require client credentials. This is
    a mandatory pre-requisite before we can progress '''
    # this is likely to run first, save the TLS credentials
    if is_state('etcd.tls.available'):
        cert_path = '/etc/ssl/flannel'
        etcd.save_client_credentials('{}/client-key.pem'.format(cert_path),
                                     '{}/client-cert.pem'.format(cert_path),
                                     '{}/client-ca.pem'.format(cert_path))

    set_state('flannel.etcd.credentials.placed')


@when_not('flannel.installed')
def install_flannel():
    ''' Unpack and install the binary release of flannel '''
    hookenv.status_set('maintenance', 'Installing flannel.')
    flannel_package = hookenv.resource_get('flannel')

    if not flannel_package:
        hookenv.status_set('blocked', 'Missing flannel resource.')
        return

    # Handle null resource publication, we check if its filesize < 1mb
    filesize = os.stat(flannel_package).st_size
    if filesize < 1000000:
        hookenv.status_set('blocked', 'Incomplete flannel resource.')
        return

    charm_dir = hookenv.charm_dir()

    # Unpack and install the flannel resource
    _unpack_resource(flannel_package, '{0}/files/flannel'.format(charm_dir))
    _install_files('./files/flannel/flanneld', '/usr/local/bin/flanneld')

    set_state('flannel.installed')


@when('flannel.installed', 'flannel.etcd.credentials.placed', 'etcd.available')
@when_not('flannel.sdn.configured')
def render_flannel_config(etcd):
    ''' Consume the flannel information, and render the config files to
    initialize flannel installation '''
    hookenv.status_set('maintenance', 'Rendering systemd files.')
    context = _build_context(etcd)
    render('flannel.service', '/lib/systemd/system/flannel.service', context)
    _initialize_etcd_with_data(etcd)
    host.service_start('flannel')
    set_state('flannel.sdn.configured')


@when('flannel.installed')
def set_flannel_version():
    ''' Surface the currently deployed version of flannel to Juju '''
    # flanneld -version
    # v0.6.1
    cmd = 'flanneld -version'
    p = Popen(cmd, shell=True,
              stdin=PIPE,
              stdout=PIPE,
              stderr=STDOUT,
              close_fds=True)
    version = p.stdout.read()
    if version:
        hookenv.application_version_set(version.split(b'v')[-1].rstrip())


@when('flannel.sdn.configured', 'host.connected')
@when_not('flannel.host.relayed')
def relay_sdn_configuration(plugin_host):
    ''' send the flannel interface configuration to the principal unit '''
    try:
        subnet, mtu = _ingest_network_config()
        cidr = hookenv.config('cidr')
        plugin_host.set_configuration(mtu, subnet, cidr)
        set_state('flannel.host.relayed')
        hookenv.status_set('active', 'Flannel subnet {0}'.format(subnet))
    except TypeError:
        hookenv.status_set('waiting', 'Flannel is starting up.')


@hook('upgrade-charm')
def reset_states_and_redeploy():
    ''' Remove state and redeploy '''
    remove_state('flannel.host.relayed')
    remove_state('flannel.installed')


@hook('stop')
def cleanup_deployment():
    ''' Terminate services, and remove the deployed bins '''

    host.service_stop('flannel')
    files = ['/usr/local/bin/flanneld',
             '/lib/systemd/system/flannel']
    for f in files:
        if os.path.exists(f):
            os.remove(f)
