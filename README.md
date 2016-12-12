# Flannel Charm

Flannel is a virtual network that gives a subnet to each host for use with
container runtimes.

This charm will deploy flannel as a background service, and configure CNI for
use with flannel, on any principal charm that implement the
[`kubernetes-cni`](https://github.com/wwwtyro/interface-kubernetes-cni) interface.


## Usage

The flannel charm is a
[subordinate](https://jujucharms.com/docs/stable/authors-subordinate-services).
This charm will require a principal charm that implements the `kubernetes-cni`
interface in order to properly deploy.

```
juju deploy flannel
juju deploy etcd
juju deploy kubernetes-master
juju add-relation flannel kubernetes-master
juju add-relation flannel etcd
```

## Configuration

**iface** The interface to configure the flannel SDN binding. If this value is
empty string or undefined the code will attempt to find the default network
adapter similar to the following command:  
```bash
route | grep default | head -n 1 | awk {'print $8'}
```

**cidr** The network range to configure the flannel SDN to declare when
establishing networking setup with etcd. Ensure this network range is not active
on the vlan you're deploying to, as it will cause collisions and odd behavior
if care is not taken when selecting a good CIDR range to assign to flannel.


## Known Limitations

This subordinate does not support being co-located with other deployments of
the flannel subordinate (to gain 2 vlans on a single application). If you
require this support please file a bug.

This subordinate also leverages juju-resources, so it is currently only available
on juju 2.0+ controllers.


## Further information

- [Flannel Homepage](https://coreos.com/flannel/docs/latest/flannel-config.html)
- [Flannel Charm Issue Tracker]()
- [Flannel Issue Tracker]()
