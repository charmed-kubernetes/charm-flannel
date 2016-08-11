# Flannel Charm Layer

A minimal layer intended to be "mixed in" with the 
[Docker Layer](http://github.com/juju-solutions/layer-docker)
to provide Flannel based Overlay Networking to Docker Container infrastructure

This will probably go away into a more generic libnetwork abstraction when
docker 1.9 launches.

## Configuration

**iface** The interface to configure the flannel SDN binding. If this value is
empty string or undefined the code will attempt to find the default network 
adapter similar to the following command:  
```bash 
route | grep default | head -n 1 | awk {'print $8'}
```
