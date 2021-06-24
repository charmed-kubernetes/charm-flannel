# Flannel Charm

Flannel is a virtual network that gives a subnet to each host for use with
container runtimes.

This charm will deploy flannel as a background service, and configure CNI for
use with flannel, on any principal charm that implements the
[`kubernetes-cni`](https://github.com/juju-solutions/interface-kubernetes-cni) interface.

This charm is maintained along with the components of Charmed Kubernetes. For full information,
please visit the [official Charmed Kubernetes docs](https://www.ubuntu.com/kubernetes/docs/charm-flannel).

# Developers

## Building the charm

```
charm build -o <build-dir>
```

## Building the flannel resources

```
./build-flannel-resources.sh
```