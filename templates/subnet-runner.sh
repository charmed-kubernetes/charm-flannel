#!/bin/bash

set -eux

docker -H {{ socket }} run \
        --net=host \
        --rm \
        {{ etcd_image }} \
        etcdctl -C "{{ connection_string }}" set /coreos.com/network/config "{ \"Network\": \"{{ cidr }}\", \"Backend\": {\"Type\": \"vxlan\"}}"
