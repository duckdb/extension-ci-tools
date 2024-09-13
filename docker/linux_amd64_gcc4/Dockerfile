FROM quay.io/pypa/manylinux2014_x86_64:latest

RUN yum install -y epel-release -y pkgconfig

# TODO do we need this?
RUN git config --global --add safe.directory '*'

# TODO do we need these?
RUN yum install -y gcc-c++
RUN yum install -y nodejs

# Setup the basic necessities
RUN yum install -y curl zip unzip tar
RUN yum install -y ninja-build
RUN yum install -y perl-IPC-Cmd
RUN yum install -y ccache
RUN yum install -y java-11-openjdk-devel maven
RUN yum install -y libgcc*i686 libstdc++*i686 glibc*i686 libgfortran*i686

# Configure Parser tools
RUN yum install -y bison flex

# Configure Rust
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

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