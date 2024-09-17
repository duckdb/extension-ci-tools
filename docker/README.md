# DuckDB docker images
DuckDB uses Docker images to build linux binaries in a flexible and reproducible way. These images can be used 
to compile a DuckDB binary (both extensions and the duckdb shell). To use an image, first build it:

```shell
docker build \ 
  --build-arg 'vcpkg_url=https://github.com/microsoft/vcpkg.git' \
  --build-arg 'vcpkg_commit=a1a1cbc975abf909a6c8985a6a2b8fe20bbd9bd6' \
  -t duckdb/linux_amd64_gcc4 \ 
  ./docker/linux_amd64_gcc4
```

Then to start building your extension:
```shell
docker run -it -v <path_to_duckdb_or_extension>:/duckdb_build_dir duckdb/linux_amd64_gcc4 make 
```