#!/usr/bin/env python3
# pip install base58 ecdsa
# pip install eth-utils
# pip install eth-hash[pycryptodome]

import binascii
import hashlib
import hmac
import struct
import ecdsa

from base58 import b58encode_check
from ecdsa.curves import SECP256k1

from eth_utils import keccak, to_checksum_address


from dotenv import load_dotenv
import os

load_dotenv()

BIP39_PBKDF2_ROUNDS = 2048
BIP39_SALT_MODIFIER = "mnemonic"
BIP32_PRIVDEV = 0x80000000
BIP32_CURVE = SECP256k1
BIP32_SEED_MODIFIER = b'Bitcoin seed'
ETH_DERIVATION_PATH = "m/44'/60'/0'/0/"


def mnemonic_to_bip39seed(mnemonic, passphrase):
    """ BIP39 seed from a mnemonic key.
        Logic adapted from https://github.com/trezor/python-mnemonic. """
    mnemonic = bytes(mnemonic, 'utf8')
    salt = bytes(BIP39_SALT_MODIFIER + passphrase, 'utf8')
    return hashlib.pbkdf2_hmac('sha512', mnemonic, salt, BIP39_PBKDF2_ROUNDS)


def bip39seed_to_bip32masternode(seed):
    """ BIP32 master node derivation from a bip39 seed.
        Logic adapted from https://github.com/satoshilabs/slips/blob/master/slip-0010/testvectors.py. """
    k = seed
    h = hmac.new(BIP32_SEED_MODIFIER, seed, hashlib.sha512).digest()
    key, chain_code = h[:32], h[32:]
    return key, chain_code


def derive_public_key(private_key):
    """ Public key from a private key. 
        Logic adapted from https://github.com/satoshilabs/slips/blob/master/slip-0010/testvectors.py. """

    Q = int.from_bytes(private_key, byteorder='big') * BIP32_CURVE.generator
    xstr = Q.x().to_bytes(32, byteorder='big')
    parity = Q.y() & 1
    return (2 + parity).to_bytes(1, byteorder='big') + xstr


def derive_bip32childkey(parent_key, parent_chain_code, i):
    """ Derives a child key from an existing key, i is current derivation parameter.
        Logic adapted from https://github.com/satoshilabs/slips/blob/master/slip-0010/testvectors.py. """

    assert len(parent_key) == 32
    assert len(parent_chain_code) == 32
    k = parent_chain_code
    if (i & BIP32_PRIVDEV) != 0:
        key = b'\x00' + parent_key
    else:
        key = derive_public_key(parent_key)
    d = key + struct.pack('>L', i)
    while True:
        h = hmac.new(k, d, hashlib.sha512).digest()
        key, chain_code = h[:32], h[32:]
        a = int.from_bytes(key, byteorder='big')
        b = int.from_bytes(parent_key, byteorder='big')
        key = (a + b) % BIP32_CURVE.order
        if a < BIP32_CURVE.order and key != 0:
            key = key.to_bytes(32, byteorder='big')
            break
        d = b'\x01' + h[32:] + struct.pack('>L', i)

    return key, chain_code


def fingerprint(public_key):
    """ BIP32 fingerprint formula, used to get b58 serialized key. """

    return hashlib.new('ripemd160', hashlib.sha256(public_key).digest()).digest()[:4]


def b58xprv(parent_fingerprint, private_key, chain, depth, childnr):
    """ Private key b58 serialization format. """

    raw = (
        b'\x04\x88\xad\xe4' +
        bytes(chr(depth), 'utf-8') +
        parent_fingerprint +
        childnr.to_bytes(4, byteorder='big') +
        chain +
        b'\x00' +
        private_key)

    return b58encode_check(raw)


def b58xpub(parent_fingerprint, public_key, chain, depth, childnr):
    """ Public key b58 serialization format. """

    raw = (
        b'\x04\x88\xb2\x1e' +
        bytes(chr(depth), 'utf-8') +
        parent_fingerprint +
        childnr.to_bytes(4, byteorder='big') +
        chain +
        public_key)

    return b58encode_check(raw)


def parse_derivation_path(str_derivation_path):
    """ Parses a derivation path such as "m/44'/60/0'/0" and returns 
        list of integers for each element in path. """

    path = []
    if str_derivation_path[0:2] != 'm/':
        raise ValueError(
            "Can't recognize derivation path. It should look like \"m/44'/60/0'/0\".")

    for i in str_derivation_path.lstrip('m/').split('/'):
        if "'" in i:
            path.append(BIP32_PRIVDEV + int(i[:-1]))
        else:
            path.append(int(i))
    return path


def mnemonic_to_private_key(mnemonic, str_derivation_path=ETH_DERIVATION_PATH, passphrase=""):
    """ Performs all convertions to get a private key from a mnemonic sentence, including:

            BIP39 mnemonic to seed
            BIP32 seed to master key
            BIP32 child derivation of a path provided

        Parameters:
            mnemonic -- seed wordlist, usually with 24 words, that is used for ledger wallet backup
            str_derivation_path -- string that directs BIP32 key derivation, defaults to path
                used by ledger ETH wallet 

    """

    derivation_path = parse_derivation_path(str_derivation_path)

    bip39seed = mnemonic_to_bip39seed(mnemonic, passphrase)

    master_private_key, master_chain_code = bip39seed_to_bip32masternode(
        bip39seed)

    private_key, chain_code = master_private_key, master_chain_code

    for i in derivation_path:
        private_key, chain_code = derive_bip32childkey(
            private_key, chain_code, i)

    # hexadecimal characters to a bytes object then encoding to convert this bytes object to a string
    private_key = str(binascii.hexlify(private_key), 'utf-8')

    return private_key


def generate_ethereum_address(private_key):
    # Convert private key from hex string to bytes
    private_key_bytes = bytes.fromhex(private_key)

    # Get public key from private key
    public_key_bytes = ecdsa.SigningKey.from_string(
        private_key_bytes, curve=ecdsa.SECP256k1).verifying_key.to_string()

    # Hash public key with Keccak-256
    keccak_hash = keccak(public_key_bytes)

    # Take last 20 bytes of hash (i.e. address)
    address = keccak_hash[-20:]

    # Convert address to checksummed hex string
    address_hex = to_checksum_address(address)

    return address_hex


def batch_generate_ethereum_account_from_mnemonic(mnemonic, number):
    account = []
    for i in range(number):
        str_derivation_path = f"m/44'/60'/0'/0/{i}"
        private_key = mnemonic_to_private_key(mnemonic, str_derivation_path)
        address = generate_ethereum_address(private_key)
        # print(f"account: {i+1}")
        # print(f"private key is: {private_key}")
        # print(f"address is: {address}\n")
        account.append((address, private_key))
        with open(".env", "a")as f:
            # f.write(f"ACCOUNT_{i}_ADDRESS={address}\n")
            f.write(f"ACCOUNT_{i}_PRIVATE_KEY={private_key}\n")

    return account


if __name__ == '__main__':
    # test mnemonic
    mnemonic = "distance replace obvious camera math express vacant reopen notice marble social page alley retire visa hockey title attract chunk secret pottery zoo caught poverty"
    accouont_list = batch_generate_ethereum_account_from_mnemonic(
        mnemonic, 300)

    for i in range(300):
        print(os.environ.get(f"ACCOUNT_{i}_PRIVATE_KEY"))
