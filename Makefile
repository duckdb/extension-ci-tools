.PHONY: test build clean

test:
	go test ./...

build:
	mkdir -p build
	go build -o build/extbuild ./cmd/extbuild

clean:
	rm -rf build

.PHONY: tidy

tidy:
	go mod tidy
