import argparse
import shutil

# Metadata block is always 256 bytes; the signature size varies (RSA-2048/3072/4096 -> 256/384/512).
METADATA_SIZE = 256
SIGNATURE_SIZE_RSA2048 = 256
LEGACY_SIGNATURE_SLOT_SIZE = 256
SUPPORTED_SIGNATURE_SIZES = [256, 384, 512]

def unsigned_leb128(value):
    # Minimal unsigned LEB128 encoding, as used by the WebAssembly binary format.
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)

def footer_size(signature_size):
    # Bytes after the Wasm header: [meta][sig 256] for legacy, else [M1][sig][M2 = M1][256 zeros].
    if signature_size == SIGNATURE_SIZE_RSA2048:
        return METADATA_SIZE + signature_size
    return 2 * METADATA_SIZE + signature_size + LEGACY_SIGNATURE_SLOT_SIZE

def start_signature(signature_size):
    # This is needed so that Wasm binaries are valid
    encoded_string = ''.encode('ascii')
    inner_size = unsigned_leb128(footer_size(signature_size))
    # 0 for custom section
    encoded_string += int(0).to_bytes(1, byteorder='big')
    # Outer section length: 1 (name length) + 16 (name) + inner size field + footer
    encoded_string += unsigned_leb128(1 + 16 + len(inner_size) + footer_size(signature_size))
    # 10 in hex = 16 in decimal, lenght of name, 1 byte
    encoded_string += int(16).to_bytes(1, byteorder='big')
    # the name of the WebAssembly custom section, 16 bytes
    encoded_string += b'duckdb_signature'
    # Inner footer size
    encoded_string += inner_size
    return encoded_string

def padded_byte_string(input):
    encoded_string = input.encode('ascii')
    encoded_string += b'\x00' * (32 - len(encoded_string))
    return encoded_string

def main():
    arg_parser = argparse.ArgumentParser(description='Append extension metadata to loadable DuckDB extensions')

    arg_parser.add_argument('-l','--library-file', type=str, help='Path to the raw shared library', required=True)
    arg_parser.add_argument('-n', '--extension-name', type=str, help='Extension name to use', required=True)

    arg_parser.add_argument('-o', '--out-file', type=str, help='Explicit path for the output file', default='')

    # The platform
    arg_parser.add_argument('-p', '--duckdb-platform', type=str, help='The DuckDB platform to encode')
    arg_parser.add_argument('-pf', '--duckdb-platform-file', type=str, help='The file containing the DuckDB platform to encode')

    arg_parser.add_argument('-dv', '--duckdb-version', type=str, help='The DuckDB version to encode, depending on the ABI type '
                                                               'this encodes the duckdb version or the C API version', required=True)
    arg_parser.add_argument('-ev', '--extension-version', type=str, help='The Extension version to encode')
    arg_parser.add_argument('-evf', '--extension-version-file', type=str, help='The file containing the Extension version to encode')

    arg_parser.add_argument('--abi-type', type=str, help='The ABI type to encode, set to C_STRUCT by default', default='C_STRUCT')

    arg_parser.add_argument('--signature-size', type=int, choices=SUPPORTED_SIGNATURE_SIZES,
                            default=SIGNATURE_SIZE_RSA2048,
                            help='Size in bytes of the trailing signature slot: 256 for RSA-2048 (default), '
                                 '384 for RSA-3072, 512 for RSA-4096. For non-default sizes the size is written as a '
                                 'marker into reserved field 6 and the metadata is repeated after the signature, '
                                 'followed by a zeroed 256-byte legacy slot.')

    args = arg_parser.parse_args()

    # FIELD7 (reader field 6) marker: empty keeps legacy RSA-2048 output byte-identical, else the size in decimal.
    SIGNATURE_SIZE = args.signature_size
    SIGNATURE_MARKER = "" if SIGNATURE_SIZE == SIGNATURE_SIZE_RSA2048 else str(SIGNATURE_SIZE)

    OUTPUT_FILE = args.out_file if args.out_file else args.extension_name + '.duckdb_extension'
    OUTPUT_FILE_TMP = OUTPUT_FILE + ".tmp"

    print("Creating extension binary:")

    # Start with copying the library to a tmp file
    print(f" - Input file: {args.library_file}")
    print(f" - Output file: {OUTPUT_FILE}")
    shutil.copyfile(args.library_file, OUTPUT_FILE_TMP)

    # Handle the platform
    PLATFORM = ""
    if args.duckdb_platform:
        PLATFORM = args.duckdb_platform
    elif args.duckdb_platform_file:
        try:
            with open(args.duckdb_platform_file, 'r') as file:
                PLATFORM = file.read().strip()
        except Exception as e:
            print(f"Failed to read platform from file {args.duckdb_platform_file}")
            raise
        if not PLATFORM:
            raise Exception(f"Platform file passed to script is empty: {args.duckdb_platform_file}")
    else:
        raise Exception(f"Neither --duckdb-platform nor --duckdb-platform-file found, please specify the platform using either")

    EXTENSION_VERSION = ""
    if args.extension_version:
        EXTENSION_VERSION = args.extension_version
    elif args.extension_version_file:
        try:
            with open(args.extension_version_file, 'r') as file:
                EXTENSION_VERSION = file.read().strip()
        except Exception as e:
            print(f"Failed to read extension version from file {args.extension_version_file}")
            raise
        if not EXTENSION_VERSION:
            raise Exception(f"Extension version file passed to script is empty: {args.extension_version_file}")
    else:
        raise Exception(f"Neither --extension-version nor --extension-version-file found, please specify the extension version using either")



    # Then append the metadata to the tmp file
    print(f" - Metadata:")
    with open(OUTPUT_FILE_TMP, 'ab') as file:
        file.write(start_signature(SIGNATURE_SIZE))
        metadata_start = file.tell()
        print(f"   - FIELD8 (unused)            = EMPTY")
        file.write(padded_byte_string(""))
        print(f"   - FIELD7 (signature size)    = {SIGNATURE_MARKER if SIGNATURE_MARKER else 'EMPTY'}")
        file.write(padded_byte_string(SIGNATURE_MARKER))
        print(f"   - FIELD6 (unused)            = EMPTY")
        file.write(padded_byte_string(""))
        print(f"   - FIELD5 (abi_type)          = {args.abi_type}")
        file.write(padded_byte_string(args.abi_type))
        print(f"   - FIELD4 (extension_version) = {EXTENSION_VERSION}")
        file.write(padded_byte_string(EXTENSION_VERSION))
        print(f"   - FIELD3 (duckdb_version)    = {args.duckdb_version}")
        file.write(padded_byte_string(args.duckdb_version))
        print(f"   - FIELD2 (duckdb_platform)   = {PLATFORM}")
        file.write(padded_byte_string(PLATFORM))
        print(f"   - FIELD1 (header signature)  = 4 (special value to identify a duckdb extension)")
        file.write(padded_byte_string("4"))

        # Write some empty space for the signature
        file.write(b"\x00" * SIGNATURE_SIZE)

    if SIGNATURE_SIZE != SIGNATURE_SIZE_RSA2048:
        # Repeat the metadata at the fixed tail position, then the zeroed legacy signature slot
        with open(OUTPUT_FILE_TMP, 'rb') as file:
            file.seek(metadata_start)
            metadata = file.read(METADATA_SIZE)
        with open(OUTPUT_FILE_TMP, 'ab') as file:
            file.write(metadata)
            file.write(b"\x00" * LEGACY_SIGNATURE_SLOT_SIZE)


    # Finally we mv the tmp file to complete the process
    shutil.move(OUTPUT_FILE_TMP, OUTPUT_FILE)

if __name__ == '__main__':
    main()