#### Setup VCPKG to correct version 2026.06.24 tag is cd61e1e26a038e82d6550a3ebbe0fbbfe7da78e3
vcpkg/scripts/buildsystems/vcpkg.cmake:
	if [ -d vcpkg/.git ]; then \
		git -C vcpkg fetch --tags && \
		git -C vcpkg checkout --detach 2026.06.24; \
	else \
		git clone --branch 2026.06.24 https://github.com/microsoft/vcpkg; \
	fi
	cd vcpkg && ./bootstrap-vcpkg.sh

setup-vcpkg: vcpkg/scripts/buildsystems/vcpkg.cmake
	@echo 'Consider exporting VCPKG_TOOLCHAIN_PATH=$(PWD)/vcpkg/scripts/buildsystems/vcpkg.cmake'

cleanup-vcpkg:
	rm -rf vcpkg
