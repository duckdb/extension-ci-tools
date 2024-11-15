# Reusable makefile for the C/C++ extensions targeting the C extension API
#
# Inputs
#   EXTENSION_NAME               : name of the extension (lower case)
#   EXTENSION_LIB_FILENAME       : the library name that is produced by the build
#	MINIMUM_DUCKDB_VERSION       : full version tag (including v)
#	MINIMUM_DUCKDB_VERSION_MAJOR : major version
#	MINIMUM_DUCKDB_VERSION_MINOR : minor version
#	MINIMUM_DUCKDB_VERSION_PATCH : patch version
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
CMAKE_VERSION_PARAMS += -DMINIMUM_DUCKDB_VERSION_MAJOR=$(MINIMUM_DUCKDB_VERSION_MAJOR)
CMAKE_VERSION_PARAMS += -DMINIMUM_DUCKDB_VERSION_MINOR=$(MINIMUM_DUCKDB_VERSION_MINOR)
CMAKE_VERSION_PARAMS += -DMINIMUM_DUCKDB_VERSION_PATCH=$(MINIMUM_DUCKDB_VERSION_PATCH)

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

MAKE_INVOCATION = make

ifeq ($(GEN),ninja)
	CMAKE_BUILD_FLAGS += -G "Ninja"
	MAKE_INVOCATION = ninja
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

CMAKE_WRAPPER=
EXTRA_CMAKE_FLAGS ?=

ifneq ($(DUCKDB_WASM_PLATFORM),)
	CMAKE_WRAPPER=emcmake
	EXTRA_CMAKE_FLAGS += -DCMAKE_C_FLAGS="$(CMAKE_C_FLAGS) -fPIC"
	ifeq ($(DUCKDB_WASM_PLATFORM), 'wasm_eh')
		EXTRA_CMAKE_FLAGS += -DCMAKE_CXX_FLAGS="$(CMAKE_CXX_FLAGS) -fPIC"
	endif
	ifeq ($(DUCKDB_WASM_PLATFORM), 'wasm_eh')
		EXTRA_CMAKE_FLAGS += -DCMAKE_CXX_FLAGS="$(CMAKE_CXX_FLAGS) -fPIC -fwasm-exceptions"
	endif
	ifeq ($(DUCKDB_WASM_PLATFORM), 'wasm_threads')
		EXTRA_CMAKE_FLAGS += -DCMAKE_CXX_FLAGS="$(CMAKE_CXX_FLAGS) -fPIC -fwasm-exceptions -DWITH_WASM_THREADS=1 -DWITH_WASM_SIMD=1 -DWITH_WASM_BULK_MEMORY=1 -pthread"
	endif
endif

#############################################
### Build targets
#############################################

build_extension_library_debug: check_configure
	$(CMAKE_WRAPPER) cmake $(CMAKE_BUILD_FLAGS) -DCMAKE_BUILD_TYPE=Debug -S $(PROJ_DIR) -B cmake_build/debug $(EXTRA_CMAKE_FLAGS)
	$(MAKE_INVOCATION) -C cmake_build/debug
	$(EXTRA_COPY_STEP_DEBUG)
	$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./build/$(DUCKDB_WASM_PLATFORM)/debug/extension/$(EXTENSION_NAME)').mkdir(parents=True, exist_ok=True)"
	$(PYTHON_VENV_BIN) -c "import shutil;shutil.copyfile('$(OUTPUT_LIB_PATH_DEBUG)', 'build/$(DUCKDB_WASM_PLATFORM)/debug/$(EXTENSION_LIB_FILENAME)')"

build_extension_library_release: check_configure
	$(CMAKE_WRAPPER) cmake $(CMAKE_BUILD_FLAGS) -DCMAKE_BUILD_TYPE=Release -S $(PROJ_DIR) -B cmake_build/release $(EXTRA_CMAKE_FLAGS)
	$(MAKE_INVOCATION) -C cmake_build/release
	$(EXTRA_COPY_STEP_RELEASE)
	$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./build/$(DUCKDB_WASM_PLATFORM)/release/extension/$(EXTENSION_NAME)').mkdir(parents=True, exist_ok=True)"
	$(PYTHON_VENV_BIN) -c "import shutil;shutil.copyfile('$(OUTPUT_LIB_PATH_RELEASE)', 'build/$(DUCKDB_WASM_PLATFORM)/release/$(EXTENSION_LIB_FILENAME)')"

#############################################
### Misc
#############################################
# TODO: switch this to use the $(MINIMUM_DUCKDB_VERSION) after v1.2.0 is released
BASE_HEADER_URL=https://raw.githubusercontent.com/duckdb/duckdb/refs/heads/main/src/include
DUCKDB_C_HEADER_URL=$(BASE_HEADER_URL)/duckdb.h
DUCKDB_C_EXTENSION_HEADER_URL=$(BASE_HEADER_URL)/duckdb_extension.h

update_duckdb_headers:
	$(PYTHON_VENV_BIN) -c "import urllib.request;urllib.request.urlretrieve('$(DUCKDB_C_HEADER_URL)', 'duckdb_capi/duckdb.h')"
	$(PYTHON_VENV_BIN) -c "import urllib.request;urllib.request.urlretrieve('$(DUCKDB_C_EXTENSION_HEADER_URL)', 'duckdb_capi/duckdb_extension.h')"

clean_cmake:
	rm -rf cmake_build
