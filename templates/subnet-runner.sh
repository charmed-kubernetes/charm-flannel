#!/bin/bash

set -eux


docker -H {{ socket }} run \
        --net=host \
        --rm \
        {% if cert_path -%}
        -v {{ cert_path }}:/tls \
        -e ETCDCTL_CA_FILE=/tls/client-ca.pem \
        -e ETCDCTL_CERT_FILE=/tls/client-cert.pem \
        -e ETCDCTL_KEY_FILE=/tls/client-key.pem \
        {%- endif %}
        {{ etcd_image }} \
        etcdctl -C "{{ connection_string }}" set /coreos.com/network/config "{ \"Network\": \"{{ cidr }}\", \"Backend\": {\"Type\": \"vxlan\"}}"
