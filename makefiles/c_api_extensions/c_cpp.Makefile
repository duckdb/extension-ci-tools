# Reusable makefile for the C/C++ extensions targeting the C extension API
#
# Inputs
#   EXTENSION_NAME               : name of the extension (lower case)
#   EXTENSION_LIB_FILENAME       : the library name that is produced by the build
# 	USE_UNSTABLE_C_API           : if set to 1, will allow usage of the unstable C API. (This pins the produced binaries to the exact DuckDB version)
#	TARGET_DUCKDB_VERSION        : full version tag (including v)
#	TARGET_DUCKDB_VERSION_MAJOR  : target major version
#	TARGET_DUCKDB_VERSION_MINOR  : target minor version
#	TARGET_DUCKDB_VERSION_PATCH  : target patch version
#	CMAKE_EXTRA_BUILD_FLAGS      : additional CMake flags to pass
#	VCPKG_TOOLCHAIN_PATH         : path to vcpkg toolchain
#	VCPKG_TARGET_TRIPLET         : vcpkg triplet to override
#	GEN                          : allow specifying ninja as generator

.PHONY: build_extension_library_debug build_extension_library_release update_duckdb_headers

#############################################
### Base config
#############################################

# Create build params to pass name and version
CMAKE_VERSION_PARAMS = -DEXTENSION_NAME=$(EXTENSION_NAME)
CMAKE_VERSION_PARAMS += -DTARGET_DUCKDB_VERSION_MAJOR=$(TARGET_DUCKDB_VERSION_MAJOR)
CMAKE_VERSION_PARAMS += -DTARGET_DUCKDB_VERSION_MINOR=$(TARGET_DUCKDB_VERSION_MINOR)
CMAKE_VERSION_PARAMS += -DTARGET_DUCKDB_VERSION_PATCH=$(TARGET_DUCKDB_VERSION_PATCH)

ifeq ($(USE_UNSTABLE_C_API),1)
	CMAKE_VERSION_PARAMS += -DDUCKDB_EXTENSION_API_VERSION_UNSTABLE=$(TARGET_DUCKDB_VERSION)
endif

CMAKE_BUILD_FLAGS = $(CMAKE_VERSION_PARAMS) $(CMAKE_EXTRA_BUILD_FLAGS)

#############################################
### Vcpkg
#############################################

ifneq ("${VCPKG_TOOLCHAIN_PATH}", "")
	CMAKE_BUILD_FLAGS += -DCMAKE_TOOLCHAIN_FILE='${VCPKG_TOOLCHAIN_PATH}'
endif
ifneq ("${VCPKG_TARGET_TRIPLET}", "")
	CMAKE_BUILD_FLAGS += -DVCPKG_TARGET_TRIPLET='${VCPKG_TARGET_TRIPLET}'
endif

#############################################
### Ninja
#############################################

ifeq ($(GEN),ninja)
	CMAKE_BUILD_FLAGS += -G "Ninja"
endif

#############################################
### Windows weirdness
#############################################

ifeq ($(OS),Windows_NT)
	OUTPUT_LIB_PATH_DEBUG=cmake_build/debug/Debug/$(EXTENSION_LIB_FILENAME)
	OUTPUT_LIB_PATH_RELEASE=cmake_build/release/Release/$(EXTENSION_LIB_FILENAME)
else
	OUTPUT_LIB_PATH_DEBUG=cmake_build/debug/$(EXTENSION_LIB_FILENAME)
	OUTPUT_LIB_PATH_RELEASE=cmake_build/release/$(EXTENSION_LIB_FILENAME)
endif

ifeq ($(DUCKDB_PLATFORM),windows_amd64_rtools)
	MINGW=1
endif
ifeq ($(DUCKDB_PLATFORM),windows_amd64_mingw)
	MINGW=1
endif
ifeq ($(MINGW),1)
	EXTRA_COPY_STEP_DEBUG=$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./cmake_build/debug/Debug').mkdir(parents=True, exist_ok=True);import shutil;shutil.copyfile('cmake_build/debug/lib$(EXTENSION_LIB_FILENAME)', '$(OUTPUT_LIB_PATH_DEBUG)')"
	EXTRA_COPY_STEP_RELEASE=$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./cmake_build/release/Release').mkdir(parents=True, exist_ok=True);import shutil;shutil.copyfile('cmake_build/release/lib$(EXTENSION_LIB_FILENAME)', '$(OUTPUT_LIB_PATH_RELEASE)')"
endif

#############################################
### Build targets
#############################################

build_extension_library_debug: check_configure
	cmake $(CMAKE_BUILD_FLAGS) -DCMAKE_BUILD_TYPE=Debug -S $(PROJ_DIR) -B cmake_build/debug
	cmake --build cmake_build/debug --config Debug
	$(EXTRA_COPY_STEP_DEBUG)
	$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./build/debug/extension/$(EXTENSION_NAME)').mkdir(parents=True, exist_ok=True)"
	$(PYTHON_VENV_BIN) -c "import shutil;shutil.copyfile('$(OUTPUT_LIB_PATH_DEBUG)', 'build/debug/$(EXTENSION_LIB_FILENAME)')"

build_extension_library_release: check_configure
	cmake $(CMAKE_BUILD_FLAGS) -DCMAKE_BUILD_TYPE=Release -S $(PROJ_DIR) -B cmake_build/release
	cmake --build cmake_build/release --config Release
	$(EXTRA_COPY_STEP_RELEASE)
	$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./build/release/extension/$(EXTENSION_NAME)').mkdir(parents=True, exist_ok=True)"
	$(PYTHON_VENV_BIN) -c "import shutil;shutil.copyfile('$(OUTPUT_LIB_PATH_RELEASE)', 'build/release/$(EXTENSION_LIB_FILENAME)')"

#############################################
### Misc
#############################################
# TODO: switch this to use the $(TARGET_DUCKDB_VERSION) after v1.2.0 is released
BASE_HEADER_URL=https://raw.githubusercontent.com/duckdb/duckdb/refs/heads/main/src/include
DUCKDB_C_HEADER_URL=$(BASE_HEADER_URL)/duckdb.h
DUCKDB_C_EXTENSION_HEADER_URL=$(BASE_HEADER_URL)/duckdb_extension.h

update_duckdb_headers:
	$(PYTHON_VENV_BIN) -c "import urllib.request;urllib.request.urlretrieve('$(DUCKDB_C_HEADER_URL)', 'duckdb_capi/duckdb.h')"
	$(PYTHON_VENV_BIN) -c "import urllib.request;urllib.request.urlretrieve('$(DUCKDB_C_EXTENSION_HEADER_URL)', 'duckdb_capi/duckdb_extension.h')"

clean_cmake:
	rm -rf cmake_build