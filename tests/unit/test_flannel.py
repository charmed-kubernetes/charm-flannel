from unittest.mock import MagicMock
from reactive import flannel
from charmhelpers.core import hookenv
from charms.reactive import set_state


def test_set_available():
    cni = MagicMock()
    hookenv.config.return_value = "192.168.0.0/16"
    flannel.set_available(cni)
    cni.set_config.assert_called_once_with(
        cidr="192.168.0.0/16", cni_conf_file="10-flannel.conflist"
    )
    set_state.assert_called_once_with("flannel.cni.available")


def test_series_upgrade():
    assert flannel.status.blocked.call_count == 0
    flannel.pre_series_upgrade()
    assert flannel.status.blocked.call_count == 1
