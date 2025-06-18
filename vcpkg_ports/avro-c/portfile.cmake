vcpkg_buildpath_length_warning(37)
if(VCPKG_TARGET_IS_WINDOWS)
    vcpkg_check_linkage(ONLY_STATIC_LIBRARY)
endif()


set(OPTIONAL_DUCKDB_PATCHES "")
if (VCPKG_TARGET_IS_EMSCRIPTEN)
   set(CMAKE_POSITION_INDEPENDENT_CODE ON)
   set(CMAKE_CXX_FLAGS " -fPIC ${VCPKG_CXX_FLAGS}" CACHE STRING "")
   set(CMAKE_C_FLAGS " -fPIC ${VCPKG_C_FLAGS}" CACHE STRING "")

   set(IS_CROSS_COMPILE 1)
   set(cross_compiling 1)
   set(VCPKG_CROSSCOMPILING 1)

   set(OPTIONAL_DUCKDB_PATCHES "${ADDITIONAL_PATCHES} static_link_only.patch")
endif()
separate_arguments(OPTIONAL_DUCKDB_PATCHES)

vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO Tishj/avro
    REF 35eebeaf9b3541d5bb5a53b5e1bbbf2fd58e2ba4
    SHA512 f6c54e6ba4fb4bcceb6935d1085b6e2b071c0ec0bcafa23dd989ba069c09e070f5dbc726ad4f2f76bca37ef1b8eaf327ae53b5f2c93597035f9dc9b39ad6e494
    PATCHES
        ${OPTIONAL_DUCKDB_PATCHES}
)

vcpkg_cmake_configure(
    SOURCE_PATH "${SOURCE_PATH}/lang/c"
    OPTIONS
        -DBUILD_EXAMPLES=OFF
        -DBUILD_TESTS=OFF
        -DBUILD_DOCS=OFF
)

vcpkg_cmake_install()

vcpkg_copy_pdbs()

file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/debug/include")
# the files are broken and there is no way to fix it because the snappy dependency has no pkgconfig file
file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/lib/pkgconfig" "${CURRENT_PACKAGES_DIR}/debug/lib/pkgconfig")



if(NOT VCPKG_TARGET_IS_EMSCRIPTEN)
    vcpkg_copy_tools(TOOL_NAMES avroappend avrocat AUTO_CLEAN)

    if(NOT VCPKG_TARGET_IS_WINDOWS)
        vcpkg_copy_tools(TOOL_NAMES avropipe avromod AUTO_CLEAN)
    endif()

    if(VCPKG_LIBRARY_LINKAGE STREQUAL "static" AND NOT VCPKG_TARGET_IS_WINDOWS)
        file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/bin" "${CURRENT_PACKAGES_DIR}/debug/bin")
    endif()
endif()

file(INSTALL "${SOURCE_PATH}/lang/c/LICENSE" DESTINATION "${CURRENT_PACKAGES_DIR}/share/${PORT}" RENAME copyright)
