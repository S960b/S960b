import struct,collections
p='/mnt/data/voteme_work/voteme/traffic.pcap';f=open(p,'rb');h=f.read(24);e='<' if h[:4]==bytes.fromhex('d4c3b2a1') else '>'
server_points=[]; stats=collections.Counter();syn=[];i=0
while 1:
 ph=f.read(16)
 if not ph:break
 _,_,cl,_=struct.unpack(e+'IIII',ph);d=f.read(cl)
 if len(d)<54 or d[12:14]!=b'\x08\x00':continue
 ip=d[14:];iphl=(ip[0]&15)*4
 if ip[9]!=6:continue
 tcp=ip[iphl:];sport,dport,seq,ack=struct.unpack('!HHII',tcp[:12]);fl=tcp[13];data=tcp[(tcp[12]>>4)*4:]
 if dport==8443 and (fl&2) and not (fl&16):syn.append(sport)
 if sport==8443 and data:
  stats[(data[:5].hex(),len(data))]+=1
  if data[:5]==bytes.fromhex('1603030213') and data[5]==12:
   body=data[9:]
   prefix=1
   for q in range(6):prefix+=1+body[prefix]
   n=body[prefix];b=body[prefix+1:prefix+1+n]
   assert n==65 and b[0]==4, (prefix,n)
   server_points.append((sport,int.from_bytes(b[1:33],'big'),int.from_bytes(b[33:65],'big')))
print('syn',len(syn),'server keys',len(server_points));print('top records',stats.most_common(13));print('server sample',server_points[:2]);print('last',server_points[-1])
with open('/mnt/data/voteme_work/server_points.txt','w') as f:
 for i,(port,x,y) in enumerate(server_points):f.write(f'{i} {port} {x} {y}\n')
with open('/mnt/data/voteme_work/syn.txt','w') as f:
 for p in syn:f.write(f'{p}\n')
