"""GameDataList (GameData/GameDataList.Product.110.byml.zs) helpers.

Flags are found by hash = murmur3_32(full name, seed 0), e.g. the sequence
graphs' GameDataFullName "Sequence_IsChildhood" -> 0xcea220d3 (Bool).

Save files (progress.sav etc.) are header (0x20 bytes) + per type a marker
(0, type index) followed by (hash, value) pairs, then a variable data section
starting at MetaData.SaveDataOffsetPos[file]. The loader (0x80bc26c0) looks
every hash up and keeps unknown ones aside, so adding a flag does not break
older saves, and saves made with the flag still load without it.
MetaData bookkeeping for one more scalar flag (verified against the stock
numbers, see README):
  AllDataSaveOffset = 0x20 + 8 * (all flags except Struct/BoolExp/Bool64bitKey + 34)
  -> +8 for any new Int; AllDataSaveSize +8 too.
  SaveDataOffsetPos[f], SaveDataSize[f] -> +8 when the flag is saved in file f.
"""
import struct

PATH = "GameData/GameDataList.Product.110.byml.zs"


def murmur3_32(data, seed=0):
    if isinstance(data, str):
        data = data.encode("utf-8")
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed
    n = len(data) & ~3
    for i in range(0, n, 4):
        k = struct.unpack_from("<I", data, i)[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[n:]
    k = 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if tail:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


assert murmur3_32("Sequence_IsChildhood") == 0xCEA220D3
