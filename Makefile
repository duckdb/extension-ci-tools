GO_TEST_FLAGS ?= -coverprofile=coverage.out -timeout 2s

.PHONY: test cov build clean

test:
	go test ${GO_TEST_FLAGS} ./...

cov: test
	go tool cover -html=coverage.out

build:
	mkdir -p build
	go build -o build/extbuild ./cmd/extbuild

clean:
	rm -rf build

.PHONY: tidy

tidy:
	go mod tidy
