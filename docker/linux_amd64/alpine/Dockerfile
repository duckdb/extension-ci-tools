FROM alpine:3.20

ARG DUCKDB_VER=v1.1.3
ARG vcpkg_url="https://github.com/microsoft/vcpkg.git"
ARG vcpkg_commit="a1a1cbc975abf909a6c8985a6a2b8fe20bbd9bd6"

# Install build dependencies
RUN apk update && \
    apk add --no-cache \
      bash \
      build-base \
      ccache \
      cmake \
      curl \
      git \
      g++ \ 
      linux-headers \
      make \
      ninja-build \
      perl \
      pkgconfig \
      python3 \
      rustup \
      tar \
      unzip \
      zip 

RUN ln -s /usr/lib/ninja-build/bin/ninja /usr/bin/ninja

ENV VCPKG_FORCE_SYSTEM_BINARIES=1
RUN mkdir /vcpkg && \
    cd /vcpkg && \
    git init && \
    git remote add origin $vcpkg_url && \
    git fetch origin $vcpkg_commit && \
    git checkout $vcpkg_commit && \
    ./bootstrap-vcpkg.sh
ENV VCPKG_ROOT=/vcpkg
ENV VCPKG_TOOLCHAIN_PATH=/vcpkg/scripts/buildsystems/vcpkg.cmake

# Common environment variables
ENV GEN=ninja

# Specify where we expect the extension to be mounted and use that as working dir
VOLUME /duckdb_build_dir
WORKDIR /duckdb_build_dir

# Mount for ccache to allow restoring ccache in GH actions
VOLUME /ccache_dir
ENV CCACHE_DIR=/ccache_dir
ENV CCACHE_COMPRESS=TRUE
ENV CCACHE_COMPRESSLEVEL=6
ENV CCACHE_MAXSIZE=400M

# extension-ci needs rust?

# Install the Rust toolchain
RUN rustup-init -y --default-toolchain stable

RUN git clone --branch $DUCKDB_VER https://github.com/duckdb/extension-ci-tools
