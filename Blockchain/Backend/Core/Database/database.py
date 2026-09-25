import os
import json


class BaseDB:
    def __init__(self):
        self.basepath = "Blockchain/Data"
        self.filepath = "/".join((self.basepath, self.filename))

    def fileExists(self):
        if not os.path.exists(self.filepath):
            print(f"File {self.filepath} not available")
            return False

        return True

    def read(self):
        if not os.path.exists(self.filepath):
            print(f"File {self.filepath} not available")
            return False

        with open(self.filepath, "r") as file:
            raw = file.readline()

        if len(raw) > 0:
            data = json.loads(raw)
        else:
            data = []
        return data

    def update(self, data):
        with open(self.filepath,'w+') as f:
            f.write(json.dumps(data))
        return True

    def write(self, item):
        data = self.read() # [block1, block2, block3]
        if data:
            data = data + item # [block1, block2, block3] + [block4] = [block1, block2,block3,block4]
        else:
            data = item # [block1]

        with open(self.filepath, "w+") as file:
            file.write(json.dumps(data))


class BlockchainDB(BaseDB):
    def __init__(self):
        self.filename = "uapcoin-blockchain"
        super().__init__()

    def lastBlock(self):
        data = self.read()

        if data:
            return data[-1]


# class AccountDB(BaseDB):
#     def __init__(self):
#         self.filename = "account"
#         super().__init__()


# class NodeDB(BaseDB):
#     def __init__(self):
#         self.filename = "node"
#         super().__init__()