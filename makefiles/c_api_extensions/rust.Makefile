# Reusable makefile for the Rust extensions targeting the C extension API
#
# Inputs
#   EXTENSION_NAME          : name of the extension (lower case)
#   EXTENSION_LIB_FILENAME  : the library name that is produced by the build
#   LOCAL_DUCKDB_RS_PATH    : overrides the duckdb-rs path

.PHONY: build_extension_library_debug build_extension_library_release

#############################################
### Development config
#############################################

# Allows overriding the duckdb-rs crates with a local version
CARGO_OVERRIDE_DUCKDB_RS_FLAG?=
ifneq ($(LOCAL_DUCKDB_RS_PATH),)
	CARGO_OVERRIDE_DUCKDB_RS_FLAG=--config 'patch.crates-io.duckdb.path="$(LOCAL_DUCKDB_RS_PATH)/crates/duckdb"' --config 'patch.crates-io.libduckdb-sys.path="$(LOCAL_DUCKDB_RS_PATH)/crates/libduckdb-sys"' --config 'patch.crates-io.duckdb-loadable-macros-sys.path="$(LOCAL_DUCKDB_RS_PATH)/crates/duckdb-loadable-macros-sys"'
endif

IS_EXAMPLE=

ifneq ($(DUCKDB_WASM_PLATFORM),)
	TARGET=wasm32-unknown-emscripten
	TARGET_INFO=--target $(TARGET) --example $(EXTENSION_NAME)
	IS_EXAMPLE=examples
endif

#############################################
### Rust Build targets
#############################################

build_extension_library_debug: check_configure
	DUCKDB_EXTENSION_NAME=$(EXTENSION_NAME) DUCKDB_EXTENSION_MIN_DUCKDB_VERSION=$(TARGET_DUCKDB_VERSION) cargo build $(CARGO_OVERRIDE_DUCKDB_RS_FLAG) $(TARGET_INFO)
	$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./build/$(DUCKDB_WASM_PLATFORM)/debug/extension/$(EXTENSION_NAME)').mkdir(parents=True, exist_ok=True)"
	$(PYTHON_VENV_BIN) -c "import shutil;shutil.copyfile('target/$(TARGET)/debug/$(IS_EXAMPLE)/$(EXTENSION_LIB_FILENAME)', 'build/$(DUCKDB_WASM_PLATFORM)/debug/$(EXTENSION_LIB_FILENAME)')"

build_extension_library_release: check_configure
	DUCKDB_EXTENSION_NAME=$(EXTENSION_NAME) DUCKDB_EXTENSION_MIN_DUCKDB_VERSION=$(TARGET_DUCKDB_VERSION) cargo build $(CARGO_OVERRIDE_DUCKDB_RS_FLAG) --release $(TARGET_INFO)
	$(PYTHON_VENV_BIN) -c "from pathlib import Path;Path('./build/$(DUCKDB_WASM_PLATFORM)/release/extension/$(EXTENSION_NAME)').mkdir(parents=True, exist_ok=True)"
	$(PYTHON_VENV_BIN) -c "import shutil;shutil.copyfile('target/$(TARGET)/release/$(IS_EXAMPLE)/$(EXTENSION_LIB_FILENAME)', 'build/$(DUCKDB_WASM_PLATFORM)/release/$(EXTENSION_LIB_FILENAME)')"

#############################################
### Misc
#############################################

clean_rust:
	cargo clean
