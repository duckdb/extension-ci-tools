message(WARNING "$CMAKE_HOST_SYSTEM_NAME")
message(WARNING "$CMAKE_HOST_SYSTEM_PROCESSOR")

if("$CMAKE_HOST_SYSTEM_NAME" STREQUAL "Linux")
	message(WARNING "$CMAKE_HOST_SYSTEM_NAME" STREQUAL "Linux")
endif()
if("$CMAKE_HOST_SYSTEM_PROCESSOR" STREQUAL "aarch64")
	message(WARNING "$CMAKE_HOST_SYSTEM_PROCESSOR" STREQUAL "aarch64")
endif()
if(NOT ("$CMAKE_HOST_SYSTEM_NAME" STREQUAL "Linux" AND "$CMAKE_HOST_SYSTEM_PROCESSOR" STREQUAL "aarch64"))
	message(WARNING NOT ("$CMAKE_HOST_SYSTEM_NAME" STREQUAL "Linux" AND "$CMAKE_HOST_SYSTEM_PROCESSOR" STREQUAL "aarch64"))
endif()
if(NOT ("${CMAKE_HOST_SYSTEM_NAME}" STREQUAL "Linux" AND "${CMAKE_HOST_SYSTEM_PROCESSOR}" STREQUAL "aarch64"))
    set(CMAKE_SYSTEM_NAME Linux)
    set(CMAKE_SYSTEM_PROCESSOR aarch64)
	
	# Since we are cross-compiling we need these to be set too
	set(CMAKE_SYSTEM_LIBRARY_PATH "/usr/aarch64-linux-gnu/lib")

	set(CMAKE_C_COMPILER "aarch64-linux-gnu-gcc")
	set(CMAKE_CXX_COMPILER "aarch64-linux-gnu-g++")

	if("fortran" IN_LIST TOOLCHAINS)
		set(CMAKE_Fortran_COMPILER "aarch64-linux-gnu-gfortran")
	endif()
endif()
