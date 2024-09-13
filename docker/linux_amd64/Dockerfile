FROM ubuntu:18.04

# Setup the basic necessities
RUN apt-get update -y -qq
RUN apt-get install -y -qq software-properties-common
RUN apt-get install -y -qq --fix-missing ninja-build make gcc-multilib g++-multilib libssl-dev wget openjdk-8-jdk zip maven unixodbc-dev libc6-dev-i386 lib32readline6-dev libssl-dev libcurl4-gnutls-dev libexpat1-dev gettext unzip build-essential checkinstall libffi-dev curl libz-dev openssh-client pkg-config
RUN apt-get install -y -qq ccache

# Install cmake 3.21
RUN mkdir /cmake_3_21 && \
    cd /cmake_3_21 && \
    wget https://github.com/Kitware/CMake/releases/download/v3.21.3/cmake-3.21.3-linux-x86_64.sh && \
    chmod +x cmake-3.21.3-linux-x86_64.sh && \
    ./cmake-3.21.3-linux-x86_64.sh --skip-license --prefix=/usr/local && \
    cmake --version

# Install GIT
RUN wget https://github.com/git/git/archive/refs/tags/v2.18.5.tar.gz && \
    tar xvf v2.18.5.tar.gz && \
    cd git-2.18.5 && \
    make && \
    make prefix=/usr install

# Install cross compilation tools
RUN apt-get install -y -qq gcc-aarch64-linux-gnu g++-aarch64-linux-gnu

# Configure Parser tools
RUN apt-get install -y -qq bison flex

# Install fortran
RUN apt-get install -y -qq gfortran gfortran-aarch64-linux-gnu

# Configure Rust
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN rustup toolchain install aarch64-unknown-linux-gnu

# Setup VCPKG n a mounted volume TODO: figure out how to cache this
RUN mkdir /vcpkg && \
    cd /vcpkg && \
    git init && \
    git remote add origin https://github.com/Microsoft/vcpkg.git && \
    git fetch origin a1a1cbc975abf909a6c8985a6a2b8fe20bbd9bd6 && \
    git checkout a1a1cbc975abf909a6c8985a6a2b8fe20bbd9bd6 && \
    ./bootstrap-vcpkg.sh
ENV VCPKG_ROOT=/vcpkg
ENV VCPKG_TOOLCHAIN_PATH=/vcpkg/scripts/buildsystems/vcpkg.cmake

# Common environment variables
ENV GEN=ninja

# Specify where we expect the extension to be mounted and use that as working dir
VOLUME /duckdb_build_dir
WORKDIR /duckdb_build_dir