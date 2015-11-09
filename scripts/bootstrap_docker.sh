#!/bin/bash

# THIS FILE IS PROVIDED BY JUJU. DO NOT HAND EDIT, YOUR CHANGES WILL NOT BE
# PERSISTENT

# Flannel Docker networking setup
# We're going to use flannel to set up networking between Docker daemons.
# Flannel itself (and etcd on which it relies) will run inside of Docker
# containers themselves. To achieve this, we need a separate "bootstrap" instance
# of the Docker daemon. This daemon will be started with --iptables=false so that
# it can only run containers with --net=host. That's sufficient to bootstrap our
# system.


set -e

interface=$(config-get iface)
cidr=$(config-get cidr)
connection_string=$1


if [ -f /var/run/docker-bootstrap.pid ]; then
  echo "Docker bootstrap instance pid found. Doing nothing."
  exit 0
fi

status-set maintenance "Installing Flannel networking"


# Cross Platform and does not survive reboots. :cheers:

docker -d -H unix:///var/run/docker-bootstrap.sock \
       -p /var/run/docker-bootstrap.pid \
       --iptables=false \
       --ip-masq=false \
       --bridge=none \
       --graph=/var/lib/docker-bootstrap 2> /var/log/docker-bootstrap.log 1> /dev/null &

# Give it a second
sleep 1

# Run ETCD for the coordination
# docker -H unix:///var/run/docker-bootstrap.sock run \
#        --net=host \
#        -d \
#        gcr.io/google_containers/etcd:2.0.12 \
#        /usr/local/bin/etcd \
#        --addr=127.0.0.1:4001 \
#        --bind-addr=0.0.0.0:4001 \
#        --data-dir=/var/etcd/data

# This takes longer to come online, sleep long enough to let the unit
# fully register itself.
# sleep 5

# Register the flannel VXLan

docker -H unix:///var/run/docker-bootstrap.sock run \
        --net=host \
        --rm \
        gcr.io/google_containers/etcd:2.0.12 \
        etcdctl -C "${connection_string}" set /coreos.com/network/config "{ \"Network\": \"${cidr}\", \"Backend\": {\"Type\": \"vxlan\"}}"

# Run flannel daemon to establish the overlay tunnel
flannelCID=$(docker -H unix:///var/run/docker-bootstrap.sock run \
            --restart=always \
            -d \
            --net=host\
            --privileged \
            -v /dev/net:/dev/net \
            quay.io/coreos/flannel:0.5.3 /opt/bin/flanneld -iface="${interface}" -etcd-endpoints="${connection_string}")

sleep 5

# Copy flannel env out and source it on the host
docker -H unix:///var/run/docker-bootstrap.sock cp ${flannelCID}:/run/flannel/subnet.env .
source subnet.env

# finalize flannel setup
# TODO: move this to the charm code
DOCKER_CONF="/etc/default/docker"
echo "DOCKER_OPTS=\"\$DOCKER_OPTS --mtu=${FLANNEL_MTU} --bip=${FLANNEL_SUBNET}\"" | sudo tee -a ${DOCKER_CONF}
ifconfig docker0 down
apt-get install -y bridge-utils && brctl delbr docker0 && service docker restart
