#!/usr/bin/env bash
set -eux

FLANNEL_VERSION=${FLANNEL_VERSION:-"v0.22.1"}
FLANNEL_CNI_PLUGIN_VERSION=${FLANNEL_CNI_PLUGIN_VERSION:-"v1.2.0"}
ETCD_VERSION=${ETCD_VERSION:-"v3.4.22"}

ARCH=${ARCH:-"amd64 arm64 s390x"}

build_script_commit="$(git show --oneline -q)"
temp_dir="$(readlink -f build-flannel-resources.tmp)"
rm -rf "$temp_dir"
mkdir "$temp_dir"
(cd "$temp_dir"
  git clone https://github.com/flannel-io/flannel.git flannel \
    --branch "$FLANNEL_VERSION" \
    --depth 1

  git clone https://github.com/flannel-io/cni-plugin.git cni-plugin \
    --branch "$FLANNEL_CNI_PLUGIN_VERSION" \
    --depth 1

  git clone https://github.com/etcd-io/etcd.git etcd \
    --branch "$ETCD_VERSION" \
    --depth 1

  # Grab the user id and group id of this current user.
  GROUP_ID=$(id -g)
  USER_ID=$(id -u)

  # Patch flannel builds for operation with bionic, focal, and jammy
  (cd flannel
    sed -e 's/CGO_ENABLED=1/CGO_ENABLED=0/g' -i Makefile                 # Don't ever enable CGO, even on AMD64
    sed -e 's/GOARCH=$(ARCH)/GOARCH=$(ARCH) -e TAG=$(TAG)/' -i Makefile  # pass a provided TAG through to ARCH specific docker builds
    sed -e '/udp/ s#^\/*#//#' -i main.go                                 # remove the udp backend since it's unused
  )

  for arch in $ARCH; do
    echo "Building flannel $FLANNEL_VERSION for $arch"
    (cd flannel
      TAG="${FLANNEL_VERSION}+ck1" ARCH=$arch make dist/flanneld-$arch
    )

    echo "Building cni-plugin $FLANNEL_CNI_PLUGIN_VERSION for $arch"
    docker run \
      --rm \
      -e GOFLAGS=-buildvcs=false \
      -e GOPROXY=direct \
      -v $temp_dir/cni-plugin:/cni-plugin \
      golang:1.20 \
      /bin/bash -c "cd /cni-plugin && ARCH=$arch make build_linux && chown -R ${USER_ID}:${GROUP_ID} ."

    echo "Building etcd $ETCD_VERSION for $arch"
    docker run \
      --rm \
      -e GOOS=linux \
      -e GOARCH="$arch" \
      -e GOFLAGS=-buildvcs=false \
      -v $temp_dir/etcd:/etcd \
      golang:1.19 \
      /bin/bash -c "cd /etcd && ./build && chown -R ${USER_ID}:${GROUP_ID} /etcd"

    rm -rf contents
    mkdir contents
    (cd contents
      echo "flannel-$arch $FLANNEL_VERSION" >> BUILD_INFO
      echo "etcdctl version $ETCD_VERSION" >> BUILD_INFO
      echo "built $(date)" >> BUILD_INFO
      echo "build script commit: $build_script_commit" >> BUILD_INFO
      mkdir cni-plugin
      cp "$temp_dir"/etcd/bin/etcdctl .
      cp "$temp_dir"/flannel/dist/flanneld-$arch ./flanneld
      cp "$temp_dir"/cni-plugin/dist/flannel-$arch cni-plugin/flannel
      tar -caf "$temp_dir/flannel-$arch.tar.gz" .
    )
  done
)
mv "$temp_dir"/flannel-*.tar.gz .
rm -rf "$temp_dir"
