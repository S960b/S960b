import struct,pathlib,sys
p=pathlib.Path(sys.argv[1])
b=p.read_bytes()
u32=lambda x: struct.unpack_from('<I',b,x)[0]
assert b[:16]==b'-==--==--==--==-'
h=dict(version=b[16],headersize=u32(20),entries=u32(24),blocks=u32(28),blocksize=u32(32),methods=u32(36),namelen=u32(40),compression_block_size=u32(44),dir_size=u32(48),partition_count=u32(52),flags=b[80],seeds=u32(84),unhashed=u32(96))
off=h['headersize']+h['entries']*22+h['seeds']*4+h['unhashed']*4+h['blocks']*h['blocksize']+h['methods']*h['namelen']
print('HEADER',h,'DIRECTORY INDEX',off,'SIZE',h['dir_size'])
print('Method bytes',repr(b[off-h['methods']*h['namelen']:off]))
if h['dir_size']==0: quit()
d=b[off:off+h['dir_size']]; pos=0

def ui():
 global pos
 v=struct.unpack_from('<I',d,pos)[0];pos+=4;return v

def string():
 global pos
 size=struct.unpack_from('<i',d,pos)[0];pos+=4
 if size<0:
  count=-size*2;value=d[pos:pos+count].decode('utf-16le','replace').rstrip('\0')
 else:
  count=size;value=d[pos:pos+count].decode('utf-8','replace').rstrip('\0')
 pos+=count
 return value
mount=string()
ndirs=ui(); print('mount',mount,'ndirs',ndirs)
dirs=[struct.unpack_from('<4I',d,pos+i*16) for i in range(ndirs)];pos+=16*ndirs
nfiles=ui(); print('nfiles',nfiles)
files=[struct.unpack_from('<3I',d,pos+i*12) for i in range(nfiles)];pos+=12*nfiles
nnames=ui(); print('nnames',nnames)
names=[string() for _ in range(nnames)]
print('parsed bytes',pos,'of',len(d), 'names sample',names[:25]);print('dirs[0]',dirs[0]);
found=[]
def rec(index,prefix,seen):
 if index in seen:return
 seen.add(index)
 name,child,sib,first=dirs[index]
 if name!=0xffffffff: prefix=f'{prefix}/{names[name]}'
 while first!=0xffffffff:
  fname,nxt,user=files[first]
  found.append((prefix+'/'+names[fname],user))
  first=nxt
 while child!=0xffffffff:
  rec(child,prefix,seen);child=dirs[child][2]
rec(0,mount.rstrip('/'),set())
for n,user in found:
 print(user,n)
print('FOUND',len(found))
