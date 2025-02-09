# Only on windows do we need to move the ccache executable to the location
# cl.exe is expected to be. This is because ccache is not compatible with
# the Visual Studio compiler, so we need to trick Visual Studio into using
# ccache instead.
find_program(ccache_exe ccache)
if(ccache_exe)
  file(COPY_FILE
    ${ccache_exe} ${CMAKE_BINARY_DIR}/cl.exe
    ONLY_IF_DIFFERENT)

  # By default Visual Studio generators will use /Zi which is not compatible
  # with ccache, so tell Visual Studio to use /Z7 instead.
  message(STATUS "Setting MSVC debug information format to 'Embedded'")
  set(CMAKE_MSVC_DEBUG_INFORMATION_FORMAT "$<$<CONFIG:Debug,RelWithDebInfo>:Embedded>")

  set(CMAKE_VS_GLOBALS
    "CLToolExe=cl.exe"
    "CLToolPath=${CMAKE_BINARY_DIR}"
    "UseMultiToolTask=true"
    "DebugInformationFormat=OldStyle"
  )
endif()