
from Crypto.Cipher import DES3

TAG_LENGTH = 2

def xor(a, b):
    return bytes( x^y for x,y in zip(a,b) )

def GHASH(H, A, C, blen=128):

    def pad_to_block(data, block_size):
        padding_len = (block_size - len(data) % block_size) % block_size
        return data + b'\x00' * padding_len

    def multiply_in_GF(x, y, R, bit_length):
        z = 0
        for i in range(bit_length):
            if (x >> (bit_length - 1 - i)) & 1:
                z ^= y
            if y & 1:
                y = (y >> 1) ^ R
            else:
                y >>= 1
        return z & ((1 << bit_length) - 1)



    if blen == 128:
        R = 0xE1000000000000000000000000000000
        bit_length = 128
    elif blen == 64:
        R = 0xD800000000000000
        bit_length = 64
    else:
        raise ValueError("Unsupported block length. Use blen=128 or blen=64.")

    X = 0
    H = int.from_bytes(H, 'big')

    len_A = len(A) * 8
    len_C = len(C) * 8
    
    A = pad_to_block(A, blen // 8)
    C = pad_to_block(C, blen // 8)

    for i in range(0, len(A), blen // 8):
        Ai = int.from_bytes(A[i:i + blen // 8], 'big')
        X = multiply_in_GF(X ^ Ai, H, R, bit_length)

    for i in range(0, len(C), blen // 8):
        Ci = int.from_bytes(C[i:i + blen // 8], 'big')
        X = multiply_in_GF(X ^ Ci, H, R, bit_length)

    
    lengths = (len_A << (bit_length // 2)) | len_C
    
    if blen == 128:
        X = multiply_in_GF(X ^ lengths, H, R, bit_length)
    elif blen == 64:
        X = multiply_in_GF(multiply_in_GF(X ^ len_A, H, R, bit_length)^ len_C, H, R, bit_length)

    return X.to_bytes(blen // 8, 'big')



class DES3_GCM():

    @staticmethod
    def getH(key):
        cipher = DES3.new(key, DES3.MODE_ECB)
        H = cipher.encrypt(b"\x00"*8 )
        return H

    @staticmethod
    def get_ctr(key, iv, blen=64):
        if blen not in (64, 128):
            raise ValueError("Unsupported block length. Use blen=64 or blen=128.")

        cipher = DES3.new(key, DES3.MODE_ECB)
        H = cipher.encrypt(b"\x00" * 8)
        standard_iv_length = 2 if blen == 64 else 12
        block_length = blen // 8

        if len(iv) == standard_iv_length:
            return b"\x00" * (block_length - len(iv)) + iv
        return GHASH(H, b"", iv, blen)


    def Enc(key, plaintext, associated_data, iv):
        
        cipher = DES3.new(key, DES3.MODE_ECB)
        H = cipher.encrypt(b"\x00"*8 )
        
        ctr_bytes = DES3_GCM.get_ctr(key, iv)
        ctr = int.from_bytes(ctr_bytes, 'big')
        Y0enc = cipher.encrypt(ctr_bytes)
        
        ciphertext = b""
        keystream = b""
        
        end = ctr + (len(plaintext)//8) + 16
        while ctr != end:
            ctr += 1
            keystream += cipher.encrypt(ctr.to_bytes(8, "big") )
            
        ciphertext = xor(keystream[:len(plaintext)], plaintext)
        tag = xor(GHASH(H, associated_data, ciphertext, 64), Y0enc)[:TAG_LENGTH]

        return ciphertext, tag


    def Dec(key, ciphertext, tag, associated_data, iv):
        
        cipher = DES3.new(key , DES3.MODE_ECB)
        H = cipher.encrypt(b"\x00"*8  )
        
        ctr_bytes = DES3_GCM.get_ctr(key, iv)
        ctr = int.from_bytes(ctr_bytes, 'big')
        Y0enc = cipher.encrypt(ctr_bytes)

        if len(tag) != TAG_LENGTH:
            return None
        tag_check = xor(GHASH(H, associated_data, ciphertext, 64), Y0enc)[:TAG_LENGTH]

        if tag_check != tag:
            return None
        
        plaintext = b""
        keystream = b""
        
        end = ctr + (len(ciphertext)//8) + 16
        while ctr != end:
            ctr += 1
            keystream += cipher.encrypt(ctr.to_bytes(8, "big")  )
            
        plaintext = xor(keystream[:len(ciphertext)], ciphertext)

        return plaintext
