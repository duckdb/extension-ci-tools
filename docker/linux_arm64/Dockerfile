FROM ubuntu:18.04

# Setup the basic necessities
RUN apt-get update -y -qq
RUN apt-get install -y -qq software-properties-common
RUN apt-get install -y -qq --fix-missing ninja-build make gcc-multilib g++-multilib libssl-dev wget openjdk-8-jdk zip maven unixodbc-dev libc6-dev-i386 lib32readline6-dev libssl-dev libcurl4-gnutls-dev libexpat1-dev gettext unzip build-essential checkinstall libffi-dev curl libz-dev openssh-client pkg-config
RUN apt-get install -y -qq ccache

# Setup cross compiler because GH actions does not have open source arm runners yet
RUN apt-get install -y -qq gcc-aarch64-linux-gnu g++-aarch64-linux-gnu

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

# Setup VCPKG n a mounted volume TODO: figure out how to cache this
ARG vcpkg_url
ARG vcpkg_commit
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

###
# Conditionally configure some extra dependencies
###
# a `;` separated list of extra toolchains to install (passed in like this to makes things easier through GitHub Actions)
# Note that it should start and end with a `;`
ARG extra_toolchains
ARG enable_rust

RUN echo "$extra_toolchains" > ~/extra_toolchains.txt

# Install Parser tools
RUN if [[ $extra_toolchains == *";parser-tools;"* ]]; then \
      apt-get install -y -qq bison flex; \
    fi

# Install Fortran
RUN if [[ $extra_toolchains == *";fortran;"* ]]; then \
      apt-get install -y -qq gfortran gfortran-aarch64-linux-gnu; \
    fi

# Configure Rust
RUN if [[ $extra_toolchains == *";rust;"* ]] || [[ "$enable_rust" = "1" ]]; then \
      curl https://sh.rustup.rs -sSf | bash -s -- -y ;\
    fi
ENV PATH="/root/.cargo/bin:${PATH}"

# Configure go
RUN if [[ $extra_toolchains == *";go;"* ]]; then \
      apt-get install -y -qq golang-go; \
    fi
ENV PATH="/usr/local/go/bin:${PATH}"