import struct, sys
d = open(sys.argv[1], 'rb').read()
sig, ver, nstreams, rva = struct.unpack_from('<4sIII', d, 0)
streams = {}
for i in range(nstreams):
    t, size, r = struct.unpack_from('<III', d, rva + i*12)
    streams[t] = (size, r)
def rstr(r):
    n = struct.unpack_from('<I', d, r)[0]
    return d[r+4:r+4+n].decode('utf-16le')
mods = []
size, r = streams[4]
n = struct.unpack_from('<I', d, r)[0]
for i in range(n):
    o = r + 4 + i*108
    base, sz = struct.unpack_from('<QI', d, o)
    name_rva = struct.unpack_from('<I', d, o+20)[0]
    mods.append((base, sz, rstr(name_rva).split('\\')[-1]))
def where(a):
    for b, s, nm in mods:
        if b <= a < b+s: return f"{nm}+0x{a-b:x}"
    return hex(a)
size, r = streams[6]
tid = struct.unpack_from('<I', d, r)[0]
code, flags, rec, addr, nparams = struct.unpack_from('<IIQQI', d, r+8)
print("thread", tid, "code", hex(code), "at", where(addr))
ctx_size, ctx_rva = struct.unpack_from('<II', d, r+8+152)
# CONTEXT amd64: Rsp at 0x98, Rip at 0xF8
rsp = struct.unpack_from('<Q', d, ctx_rva+0x98)[0]; rip = struct.unpack_from('<Q', d, ctx_rva+0xF8)[0]
regs = ['Rax','Rcx','Rdx','Rbx','Rsp','Rbp','Rsi','Rdi','R8','R9','R10','R11','R12','R13','R14','R15']
vals = struct.unpack_from('<16Q', d, ctx_rva+0x78)
print("rip", where(rip)); print({k: hex(v) for k, v in zip(regs, vals)})
# read stack memory for that thread from memory list (stream 5) or memory64 (9)
mem = []
if 5 in streams:
    size, r = streams[5]; n = struct.unpack_from('<I', d, r)[0]
    for i in range(n):
        start, dsz, drva = struct.unpack_from('<QII', d, r+4+i*16); mem.append((start, dsz, drva))
if 9 in streams:
    size, r = streams[9]; n, base_rva = struct.unpack_from('<QQ', d, r); cur = base_rva
    for i in range(n):
        start, dsz = struct.unpack_from('<QQ', d, r+16+i*16); mem.append((start, dsz, cur)); cur += dsz
def readmem(a, n):
    for s, sz, rv in mem:
        if s <= a and a+n <= s+sz: return d[rv+(a-s): rv+(a-s)+n]
    return None
print("--- return addresses on stack (modules only)")
seen = 0
for off in range(0, 0x4000, 8):
    b = readmem(rsp+off, 8)
    if not b: break
    v = struct.unpack('<Q', b)[0]
    w = where(v)
    if '+' in w and not w.startswith(('ntdll','KERNEL')):
        print(hex(off), w); seen += 1
        if seen > 40: break
