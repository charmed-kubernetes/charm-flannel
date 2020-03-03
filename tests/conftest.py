import sys
from unittest.mock import MagicMock


def identity(x):
    return x


charmhelpers = MagicMock()
sys.modules['charmhelpers'] = charmhelpers
sys.modules['charmhelpers.core'] = charmhelpers.core
sys.modules['charmhelpers.core.hookenv'] = charmhelpers.core.hookenv
sys.modules['charmhelpers.core.host'] = charmhelpers.core.host
sys.modules['charmhelpers.contrib'] = charmhelpers.contrib
sys.modules['charmhelpers.contrib.charmsupport'] = \
    charmhelpers.contrib.charmsupport

reactive = MagicMock()
sys.modules['charms.reactive'] = reactive
sys.modules['charms.reactive.helpers'] = reactive.helpers
reactive.when.return_value = identity
reactive.when_any.return_value = identity
reactive.when_not.return_value = identity
reactive.hook.return_value = identity

templating = MagicMock()
sys.modules['charms.templating'] = templating
sys.modules['charms.templating.jinja2'] = templating.jinja2
