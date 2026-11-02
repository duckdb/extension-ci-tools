if(NOT _DUCKDB_VCPKG_EMSCRIPTEN_THREADS_TOOLCHAIN)
	set(_DUCKDB_VCPKG_EMSCRIPTEN_THREADS_TOOLCHAIN ON)

	if(DEFINED ENV{EMSCRIPTEN_ROOT})
		set(_duckdb_emscripten_root "$ENV{EMSCRIPTEN_ROOT}")
	elseif(DEFINED ENV{EMSDK})
		set(_duckdb_emscripten_root "$ENV{EMSDK}/upstream/emscripten")
	else()
		find_path(_duckdb_emscripten_root "emcc")
	endif()

	if(NOT EXISTS "${_duckdb_emscripten_root}/cmake/Modules/Platform/Emscripten.cmake")
		message(FATAL_ERROR "Emscripten.cmake toolchain file not found")
	endif()

	include("${_duckdb_emscripten_root}/cmake/Modules/Platform/Emscripten.cmake")

	# -pthread enables the atomics and bulk-memory features that linking with shared memory requires
	string(APPEND CMAKE_C_FLAGS_INIT " -pthread")
	string(APPEND CMAKE_CXX_FLAGS_INIT " -pthread")
endif()
