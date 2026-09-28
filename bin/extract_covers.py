import struct, glob, os, sys

# FLAC METADATA_BLOCK_PICTURE (type 6) 扫描 + 提取
out_dir = sys.argv[1] if len(sys.argv) > 1 else None
if out_dir:
    os.makedirs(out_dir, exist_ok=True)

for path in sorted(glob.glob('*.flac')):
    with open(path, 'rb') as f:
        head = f.read(4)
        if head != b'fLaC':
            print('not-flac |', path); continue
        pic = None
        while True:
            b = f.read(1)
            if not b: break
            t = b[0] & 0x7f; last = b[0] & 0x80
            ln = struct.unpack('>I', f.read(4))[0]
            if t == 6:
                pic = f.read(ln); break
            f.seek(ln, 1)
            if last: break
        if not pic:
            print('no-picture |', path); continue
        off = 4
        ml = struct.unpack('>I', pic[off:off+4])[0]; off += 4 + ml
        mime = pic[off-ml:off-4+4] if False else None
        mime = pic[8:8+ml].decode('utf-8', 'replace')
        off2 = 8 + ml
        dl = struct.unpack('>I', pic[off2:off2+4])[0]
        desc = pic[off2+4:off2+4+dl].decode('utf-8', 'replace')
        p = off2 + 4 + dl
        w, h = struct.unpack('>II', pic[p:p+8])
        colors = struct.unpack('>I', pic[p+8:p+12])[0]
        dlen = struct.unpack('>I', pic[p+16:p+20])[0]
        data = pic[p+20:p+20+dlen]
        ext = 'jpg' if 'jpeg' in mime else ('png' if 'png' in mime else 'img')
        print(f'{w}x{h} {mime} {dlen}B | {path}')
        if out_dir:
            base = os.path.splitext(os.path.basename(path))[0]
            with open(os.path.join(out_dir, base + '.' + ext), 'wb') as o:
                o.write(data)
