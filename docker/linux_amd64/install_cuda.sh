#!/usr/bin/env bash

set -euo pipefail

cuda_major=$1
cuda_platform=$2

case "${cuda_major}" in
12)
	cuda_release=12.9.1
	components=(
		cuda_cccl
		cuda_cudart
		cuda_nvcc
		cuda_nvml_dev
		cuda_nvrtc
		libcurand
		libnvjitlink
	)
	;;
13)
	cuda_release=13.3.0
	components=(
		cccl
		cuda_crt
		cuda_cudart
		cuda_culibos
		cuda_nvcc
		cuda_nvml_dev
		cuda_nvrtc
		libcurand
		libnvjitlink
		libnvptxcompiler
		libnvvm
	)
	;;
*)
	echo "Unsupported CUDA major version: ${cuda_major}" >&2
	exit 1
	;;
esac

base_url=https://developer.download.nvidia.com/compute/cuda/redist
install_dir=/usr/local/cuda-${cuda_release}
download_dir=$(mktemp -d)
manifest=${download_dir}/redistrib.json
trap 'rm -rf "${download_dir}"' EXIT

curl --fail --show-error --location --retry 5 --retry-delay 2 --retry-connrefused \
	"${base_url}/redistrib_${cuda_release}.json" \
	--output "${manifest}"

mkdir -p "${install_dir}"
for component in "${components[@]}"; do
	archive_path=$(jq --exit-status --raw-output \
		--arg component "${component}" \
		--arg platform "${cuda_platform}" \
		'.[$component][$platform].relative_path' \
		"${manifest}")
	checksum=$(jq --exit-status --raw-output \
		--arg component "${component}" \
		--arg platform "${cuda_platform}" \
		'.[$component][$platform].sha256' \
		"${manifest}")
	archive=${download_dir}/${component}.tar.xz

	curl --fail --show-error --location --retry 5 --retry-delay 2 --retry-connrefused \
		"${base_url}/${archive_path}" \
		--output "${archive}"
	echo "${checksum}  ${archive}" | sha256sum --check -
	tar --extract --xz --file "${archive}" --directory "${install_dir}" --strip-components 1
	rm "${archive}"
done

ln -sfn lib "${install_dir}/lib64"
ln -sfn "${install_dir}" /usr/local/cuda
/usr/local/cuda/bin/nvcc --version
