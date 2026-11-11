set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE static)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_BUILD_TYPE release)

# Temporary: MSVC arm64 emits a broken `bl __chkstk` prologue for OpenSSL at /O2 /Gs0; drop once cl is fixed.
if(PORT STREQUAL "openssl")
	set(VCPKG_C_FLAGS_RELEASE "/O1")
	set(VCPKG_CXX_FLAGS_RELEASE "/O1")
endif()
