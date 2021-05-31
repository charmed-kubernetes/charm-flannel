import json
import unittest

from unittest import mock
from reactive import flannel
from charmhelpers.core import hookenv
from charms.reactive import set_state


def test_set_available():
    cni = mock.MagicMock()
    hookenv.config.return_value = '192.168.0.0/16'
    flannel.set_available(cni)
    cni.set_config.assert_called_once_with(
        cidr='192.168.0.0/16',
        cni_conf_file='10-flannel.conflist'
    )
    set_state.assert_called_once_with('flannel.cni.available')


def test_series_upgrade():
    assert flannel.status.blocked.call_count == 0
    flannel.pre_series_upgrade()
    assert flannel.status.blocked.call_count == 1


class TestFlannel(unittest.TestCase):
    def setUp(self):
        self.etcd = mock.MagicMock()
        self.etcd.get_connection_string.return_value = "http://1.2.3.4:2379"

    @mock.patch.object(flannel, "check_output", auto_spec=True)
    def test_run_etcdctl(self, check_output):
        check_output.return_value = "foobar"
        data = json.dumps({
            'Network': '10.1.0.0/16',
            'SubnetLen': 22,
            'Backend': {'Type': 'vxlan'}
        })

        output = flannel.run_etcdctl(self.etcd, "set",
                                     "/coreos.com/network/config", data)
        self.assertEqual(output, "foobar")
        cmd = ['etcdctl',
               '--endpoint', 'http://1.2.3.4:2379',
               '--cert-file', '/etc/ssl/flannel/client-cert.pem',
               '--key-file', '/etc/ssl/flannel/client-key.pem',
               '--ca-file', '/etc/ssl/flannel/client-ca.pem',
               'set', '/coreos.com/network/config', data]
        check_output.assert_called_with(cmd)

    @mock.patch.object(flannel, "check_call", auto_spec=True)
    @mock.patch.object(flannel, "check_output", auto_spec=True)
    @mock.patch.object(flannel, "config", auto_spec=True)
    def test_configure_network(self, config, check_output, check_call):
        def fake_config(key):
            d = {"subnet-len": 22,
                 "cidr": "10.1.0.0/16",
                 "vni": 1,
                 "port": 1}
            return d[key]

        config.side_effect = fake_config
        check_output.return_value = json.dumps({})
        flannel.configure_network(self.etcd)

        flannel_config = {"Network": "10.1.0.0/16",
                          "Backend": {"Type": "vxlan", "VNI": 1, "Port": 1},
                          "SubnetLen": 22}

        cmd_get = mock.call([
            'etcdctl',
            '--endpoint', 'http://1.2.3.4:2379',
            '--cert-file', '/etc/ssl/flannel/client-cert.pem',
            '--key-file', '/etc/ssl/flannel/client-key.pem',
            '--ca-file', '/etc/ssl/flannel/client-ca.pem',
            'get', '/coreos.com/network/config'
        ])
        cmd_set = mock.call([
            'etcdctl',
            '--endpoint', 'http://1.2.3.4:2379',
            '--cert-file', '/etc/ssl/flannel/client-cert.pem',
            '--key-file', '/etc/ssl/flannel/client-key.pem',
            '--ca-file', '/etc/ssl/flannel/client-ca.pem',
            'set', '/coreos.com/network/config',
            json.dumps(flannel_config)
        ])
        check_output.assert_has_calls([cmd_get, cmd_set])

    @mock.patch.object(flannel, "check_call", auto_spec=True)
    @mock.patch.object(flannel, "check_output", auto_spec=True)
    @mock.patch.object(flannel, "config", auto_spec=True)
    def test_configure_network_default(self, config, check_output, check_call):
        def fake_config(key):
            d = {"subnet-len": 24,
                 "cidr": "10.1.0.0/16",
                 "vni": 0,
                 "port": 0}
            return d[key]

        config.side_effect = fake_config
        check_output.return_value = json.dumps({"Network": "10.1.0.0/16"})
        flannel.configure_network(self.etcd)

        flannel_config = {"Network": "10.1.0.0/16",
                          "Backend": {"Type": "vxlan"}}

        cmd_get = mock.call([
            'etcdctl',
            '--endpoint', 'http://1.2.3.4:2379',
            '--cert-file', '/etc/ssl/flannel/client-cert.pem',
            '--key-file', '/etc/ssl/flannel/client-key.pem',
            '--ca-file', '/etc/ssl/flannel/client-ca.pem',
            'get', '/coreos.com/network/config'
        ])
        cmd_set = mock.call([
            'etcdctl',
            '--endpoint', 'http://1.2.3.4:2379',
            '--cert-file', '/etc/ssl/flannel/client-cert.pem',
            '--key-file', '/etc/ssl/flannel/client-key.pem',
            '--ca-file', '/etc/ssl/flannel/client-ca.pem',
            'set', '/coreos.com/network/config',
            json.dumps(flannel_config)
        ])
        check_output.assert_has_calls([cmd_get, cmd_set])
