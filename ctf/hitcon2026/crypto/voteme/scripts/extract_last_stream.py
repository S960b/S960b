import struct
p='/mnt/data/voteme_work/voteme/traffic.pcap';f=open(p,'rb');h=f.read(24);e='<' if h[:4]==bytes.fromhex('d4c3b2a1') else '>'
port=49726;flows={'s':{},'c':{}}
while 1:
 ph=f.read(16)
 if not ph:break
 _,_,cl,_=struct.unpack(e+'IIII',ph);d=f.read(cl)
 if len(d)<54 or d[12:14]!=b'\x08\x00':continue
 ip=d[14:];iphl=(ip[0]&15)*4
 if ip[9]!=6:continue
 tcp=ip[iphl:];sport,dport,seq,ack=struct.unpack('!HHII',tcp[:12]);data=tcp[(tcp[12]>>4)*4:]
 if sport==8443 and dport==port and data:flows['s'][seq]=data
 elif sport==port and dport==8443 and data:flows['c'][seq]=data
for side in ('s','c'):
 b=b''.join(v for _,v in sorted(flows[side].items()))
 with open('/mnt/data/voteme_work/last_'+side+'.bin','wb') as f:f.write(b)
 print(side,len(b),'first',b[:10].hex())
