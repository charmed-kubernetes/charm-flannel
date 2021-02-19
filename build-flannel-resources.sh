#!/usr/bin/env bash
set -eux

FLANNEL_VERSION=${FLANNEL_VERSION:-"v0.11.0"}
ETCD_VERSION=${ETCD_VERSION:-"v2.3.7"}

ARCH=${ARCH:-"amd64 arm64 s390x"}

build_script_commit="$(git show --oneline -q)"
temp_dir="$(readlink -f build-flannel-resources.tmp)"
rm -rf "$temp_dir"
mkdir "$temp_dir"
(cd "$temp_dir"
  git clone https://github.com/coreos/flannel.git flannel \
    --branch "$FLANNEL_VERSION" \
    --depth 1

  git clone https://github.com/coreos/etcd.git etcd \
    --branch "$ETCD_VERSION" \
    --depth 1

  # Grab the user id and group id of this current user.
  GROUP_ID=$(id -g)
  USER_ID=$(id -u)

  for arch in $ARCH; do
    echo "Building flannel $FLANNEL_VERSION for $arch"
    (cd flannel
      ARCH=$arch make dist/flanneld-$arch
    )

    echo "Building etcd $ETCD_VERSION for $arch"
    docker run \
      --rm \
      -e GOOS=linux \
      -e GOARCH="$arch" \
      -v $temp_dir/etcd:/etcd \
      golang:1.15 \
      /bin/bash -c "cd /etcd && ./build && chown -R ${USER_ID}:${GROUP_ID} /etcd"

    rm -rf contents
    mkdir contents
    (cd contents
      echo "flannel-$arch $FLANNEL_VERSION" >> BUILD_INFO
      echo "etcdctl version $ETCD_VERSION" >> BUILD_INFO
      echo "built $(date)" >> BUILD_INFO
      echo "build script commit: $build_script_commit" >> BUILD_INFO
      cp "$temp_dir"/etcd/bin/etcdctl .
      cp "$temp_dir"/flannel/dist/flanneld-$arch ./flanneld
      tar -caf "$temp_dir/flannel-$arch.tar.gz" .
    )
  done
)
mv "$temp_dir"/flannel-*.tar.gz .
rm -rf "$temp_dir"
