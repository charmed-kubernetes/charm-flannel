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


set -ex

interface=$(config-get iface)
cidr=$(config-get cidr)
connection_string=$1


if [ ! -f /var/run/docker-bootstrap.pid ]; then
  docker daemon -H unix:///var/run/docker-bootstrap.sock \
         -p /var/run/docker-bootstrap.pid \
         --iptables=false \
         --ip-masq=false \
         --bridge=none \
         --graph=/var/lib/docker-bootstrap 2> /var/log/docker-bootstrap.log 1> /dev/null &
fi

# Give it a second
sleep 1

docker -H unix:///var/run/docker-bootstrap.sock run \
        --net=host \
        --rm \
        gcr.io/google_containers/etcd:2.0.12 \
        etcdctl -C "${connection_string}" set /coreos.com/network/config "{ \"Network\": \"${cidr}\", \"Backend\": {\"Type\": \"vxlan\"}}"


flannelCID=$(docker -H unix:///var/run/docker-boostrap.sock ps -f name=flannel -q)
# Run flannel daemon to establish the overlay tunnel
if [[ "${flannelCID}" == "" ]]; then
  status-set maintenance "Installing Flannel networking"

  # Register the flannel VXLan
  flannelCID=$(docker -H unix:///var/run/docker-bootstrap.sock run \
              --restart=always \
              -d \
              --net=host\
              --privileged \
              -v /dev/net:/dev/net \
              --name=flannel
              quay.io/coreos/flannel:latest /opt/bin/flanneld -iface="${interface}" -etcd-endpoints="${connection_string}")

  sleep 6
fi

# Copy flannel env out and source it on the host
docker -H unix:///var/run/docker-bootstrap.sock cp ${flannelCID}:/run/flannel/subnet.env .

source subnet.env
