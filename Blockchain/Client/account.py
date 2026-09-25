import sys
sys.path.append("/Users/ASUS/OneDrive/Desktop/UAP Coin")

from Blockchain.Backend.Core.EllepticCurve.EllepticCurve import Sha256Point

from Blockchain.Backend.Util.util import encode_base58, decode_base58, hash160, hash160_bytes, hash256, hash256_bytes
import secrets


class Account:

    def createKeys(self):
        """Secp256k1 Curve Generator Points"""
        Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
        Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8

        """Create an Instance of Class Sha256Point"""
        G = Sha256Point(Gx, Gy)

        """ Generate Secure Private Key """
        self.privateKey = secrets.randbits(256)

        """ 
         # Multiply Private Key with Generator Point
         # Returns X-coordinate and Y-Coordinate 
        """
        unCompressedPublicKey = self.privateKey * G
        xpoint = unCompressedPublicKey.x
        ypoint = unCompressedPublicKey.y

        """ Address Prefix for Odd or even value of YPoint """
        if ypoint.num % 2 == 0:
            compressedPublicKey = b"\x02" + xpoint.num.to_bytes(32, "big")
        else:
            compressedPublicKey = b"\x03" + xpoint.num.to_bytes(32, "big")

        """ RIPEMD160 Hashing Algorithm returns the hash of Compressed Public Key"""

        """ Generating the public key """
        self.publicKey = hash160_bytes(compressedPublicKey) # 20 byte hashcode

        """Prefix for Mainnet"""
        main_prefix = b"\x00" # 1 byte prefix for mainnet

        newAddr = main_prefix + self.publicKey # 21 byte address

        """Checksum"""
        checksum = hash256_bytes(newAddr)[:4] # 4 byte checksum, taking first 4 bytes of the hash256 of newAddr

        newAddr = newAddr + checksum

        self.PublicAddress = encode_base58(newAddr)

        print(f"Private Key: {self.privateKey}")
        print(f"Public Key: {self.publicKey.hex()}")
        print(f"Public Address: {self.PublicAddress}")

        print(self.publicKey == decode_base58(self.PublicAddress))

if __name__ == "__main__":
    acct = Account()
    acct.createKeys()
