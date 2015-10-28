import os

from shlex import split
from subprocess import check_call

from charms.reactive import set_state
from charms.reactive import when
from charms.reactive import when_not

from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import status_set

from os import getenv
import sys

@when('docker.ready')
@when_not('etcd.connected')
def halt_execution():
    hookenv.status_set('blocked', 'Pending Etcd relation.')

@when('docker.ready', 'etcd.available')
def run_bootstrap_daemons(etcd):
    ''' Starts a bootstrap docker service on /var/run/docker-bootstrap.sock
        fetches ETCD, Flannel, and starts the services. This method will halt
        a running Docker daemon started by systemd/upstart. Not to be run after
        initial job completion'''

    status_set('maintenance', "Installing Flannel networking")
    cmd = "scripts/bootstrap_docker.sh {}".format(etcd.connection_string())
    check_call(split(cmd))
