#include <gmpxx.h>
#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <array>
#include <sstream>
#include <iomanip>
#include <cstdint>
#include <algorithm>
#include <chrono>
using namespace std;
const mpz_class P("21888242871839275222246405745257275088696311157297823662689037894645226208583");
const mpz_class R("21888242871839275222246405745257275088548364400416034343698204186575808495617");
const mpz_class RR=mpz_class(1)<<256;
const mpz_class ONE=1;
mpz_class mod(const mpz_class&a,const mpz_class &m){mpz_class v;mpz_mod(v.get_mpz_t(),a.get_mpz_t(),m.get_mpz_t());return v;}
mpz_class modp(const mpz_class&a){return mod(a,P);}
mpz_class modr(const mpz_class&a){return mod(a,R);}
mpz_class inv(const mpz_class&a,const mpz_class &p){mpz_class x;if(!mpz_invert(x.get_mpz_t(),a.get_mpz_t(),p.get_mpz_t()))throw runtime_error("no inverse");return x;}
struct Pt {mpz_class x,y,z;};
Pt id(){return {0,1,0};}Pt fromxy(const mpz_class &x,const mpz_class &y){return {x,y,1};}
Pt dbl(const Pt&pt){if(pt.z==0||pt.y==0)return id();auto &x=pt.x,&y=pt.y,&z=pt.z;
 mpz_class xx=modp(x*x),yy=modp(y*y),yyyy=modp(yy*yy);
 mpz_class s=modp(2*(modp((x+yy)*(x+yy))-xx-yyyy));
 mpz_class m=modp(3*xx);
 return {modp(m*m-2*s),modp(m*(s-modp(m*m-2*s))-8*yyyy),modp(2*y*z)};
}
Pt add(const Pt&a,const Pt&b){if(a.z==0)return b;if(b.z==0)return a;
 mpz_class z1=modp(a.z*a.z),z2=modp(b.z*b.z),u1=modp(a.x*z2),u2=modp(b.x*z1),s1=modp(a.y*b.z*z2),s2=modp(b.y*a.z*z1),h=modp(u2-u1),t=modp(s2-s1);
 if(h==0)return t==0?dbl(a):id();
 mpz_class hh=modp(h*h),hhh=modp(h*hh),v=modp(u1*hh),x=modp(t*t-hhh-2*v);
 return {x,modp(t*(v-x)-s1*hhh),modp(a.z*b.z*h)};
}
Pt neg(Pt p){if(p.z!=0)p.y=modp(-p.y);return p;}
Pt sub(const Pt&a,const Pt&b){return add(a,neg(b));}
Pt mul(mpz_class v,Pt p){Pt r=id();while(v>0){if(mpz_odd_p(v.get_mpz_t()))r=add(r,p);p=dbl(p);v>>=1;}return r;}
bool eq(const Pt&a,const Pt&b){if(a.z==0||b.z==0)return a.z==0&&b.z==0;
 mpz_class z1=modp(a.z*a.z),z2=modp(b.z*b.z);
 return modp(a.x*z2)==modp(b.x*z1) && modp(a.y*b.z*z2)==modp(b.y*a.z*z1);
}
mpz_class le(const unsigned char*b,int n){mpz_class t;mpz_import(t.get_mpz_t(),n,-1,1,0,0,b);return t;}
uint32_t load32(const unsigned char *p){return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);}
static inline uint32_t rotl(uint32_t x,int k){return (x<<k)|(x>>(32-k));}
struct ChaCha {
 uint32_t state[16],buff[16];int idx=16;
 ChaCha(const unsigned char*seed){
  uint32_t init[]={0x61707865,0x3320646e,0x79622d32,0x6b206574};
  for(int i=0;i<4;i++)state[i]=init[i];
  for(int i=0;i<8;i++)state[4+i]=load32(seed+4*i);
  for(int i=12;i<16;i++)state[i]=0;
 }
 static void qr(uint32_t*st,int a,int b,int c,int d){
  st[a]+=st[b];st[d]=rotl(st[d]^st[a],16);
  st[c]+=st[d];st[b]=rotl(st[b]^st[c],12);
  st[a]+=st[b];st[d]=rotl(st[d]^st[a],8);
  st[c]+=st[d];st[b]=rotl(st[b]^st[c],7);
 }
 void update(){copy(state,state+16,buff);
  for(int i=0;i<10;i++){
   qr(buff,0,4,8,12);qr(buff,1,5,9,13);qr(buff,2,6,10,14);qr(buff,3,7,11,15);
   qr(buff,0,5,10,15);qr(buff,1,6,11,12);qr(buff,2,7,8,13);qr(buff,3,4,9,14);
  }
  for(int i=0;i<16;i++)buff[i]+=state[i];idx=0;
  if(++state[12]==0)if(++state[13]==0)if(++state[14]==0)++state[15];
 }
 uint32_t next32(){if(idx==16)update();return buff[idx++];}
 uint64_t next64(){uint64_t hi=next32(),lo=next32();return (hi<<32)|lo;}
 mpz_class nextFieldRaw(){mpz_class v;for(;;){v=0;for(int j=0;j<4;j++){mpz_class w(next64());v|=(w<<(64*j));}
  mpz_fdiv_r_2exp(v.get_mpz_t(),v.get_mpz_t(),254);
  if(v<R)return v;
 }}
};

struct Precomp {
 vector<array<Pt,16>> window;
 Precomp(Pt pt){window.resize(64);
  for(int i=0;i<64;i++){
   window[i][0]=id();
   for(int j=1;j<16;j++)window[i][j]=add(window[i][j-1],pt);
   for(int k=0;k<4;k++)pt=dbl(pt);
  }
 }
 Pt operator()(mpz_class s)const{
  Pt out=id();
  for(int i=0;i<64;i++){
   unsigned long nib=mpz_get_ui(s.get_mpz_t())&15;
   if(nib)out=add(out,window[i][nib]);
   s>>=4;
  }
  return out;
 }
};
struct Record {string uuid;Pt A;};
vector<Record> loadProofs(const char*fname){
 // TSV file: each line UUID A.x A.y
 ifstream f(fname);vector<Record> rec;string uuid,x,y;
 while(f>>uuid>>x>>y)rec.push_back({uuid,fromxy(mpz_class(x),mpz_class(y))});
 return rec;
}
int main(int argc,char**argv){
 if(argc<5){cerr<<"usage: recover_votes zkey proofa.tsv seed_candidates.bin output.txt"<<endl;return 1;}
 string path=argv[1];ifstream f(path,ios::binary);vector<unsigned char>b((istreambuf_iterator<char>(f)),{});f.close();
 vector<vector<unsigned char>>section(15);size_t pos=12;
 while(pos+12<b.size()){
  uint32_t ty=load32(&b[pos]);uint64_t len=0;for(int i=0;i<8;i++)len|=(uint64_t)b[pos+4+i]<<(8*i);
  section[ty]=vector<unsigned char>(b.begin()+pos+12,b.begin()+pos+12+len);pos+=12+len;
 }
 auto &z=section[14];
 mpz_class rinv=inv(mod(RR,P),P);
 vector<Pt> PTau;
 for(size_t i=0;i<z.size();i+=64){mpz_class x=modp(le(&z[i],32)*rinv),y=modp(le(&z[i+32],32)*rinv);PTau.push_back(fromxy(x,y));}
 cout<<"PTau "<<PTau.size()<<" first generator="<<eq(PTau[0],fromxy(1,2))<<endl;
 // Root of unity for BN128 scalar Fr: nqr=5, domainSize=8
 mpz_class omega;mpz_class exponent=(R-1)/8;mpz_powm(omega.get_mpz_t(),mpz_class(5).get_mpz_t(),exponent.get_mpz_t(),R.get_mpz_t());
 cout<<"omega "<<omega<<" pow4 "<<modr(omega*omega*omega*omega)<<endl;
 mpz_class inverse8=inv(mpz_class(8),R);
 auto evalPoly=[&](int which){
  vector<mpz_class> ev={1,1337,0,0,1337,1,0,0};
  if(which==1)ev={0,0,1,1,0,0,0,0};
  vector<mpz_class> cf(8);Pt out=id();
  for(int k=0;k<8;k++){
   mpz_class sum=0;
   for(int j=0;j<8;j++){
    mpz_class po;mpz_class exp=(8-(j*k)%8)%8;
    mpz_powm(po.get_mpz_t(),omega.get_mpz_t(),exp.get_mpz_t(),R.get_mpz_t());
    sum=modr(sum+ev[j]*po);
   }
   cf[k]=modr(sum*inverse8);
   out=add(out,mul(cf[k],PTau[k]));
  }
  return out;
 };
 Pt C=evalPoly(0),V=evalPoly(1);
 cout<<"V nonzero="<<(V.z!=0)<<endl;
 // commit blinded A = C + vote*V + b2*(PTau[8]-PTau[0]) + b1*(PTau[9]-PTau[1])
 Pt D0=sub(PTau[8],PTau[0]),D1=sub(PTau[9],PTau[1]);
 Precomp d0(D0),d1(D1);
 mpz_class fr_mont_inverse=inv(mod(RR,R),R);
 vector<Pt> bases={C,add(C,V),add(add(C,V),V)};
 auto proofs=loadProofs(argv[2]);
 FILE*fi=fopen(argv[3],"rb");uint32_t count=0;fread(&count,4,1,fi);
 cout<<"proofs="<<proofs.size()<<" candidates="<<count<<" Rinv="<<fr_mont_inverse<<endl;
 ofstream output(argv[4]);auto start=chrono::steady_clock::now();
 for(int i=0;i<(int)count;i++){
  int found=0;int val=-1;int chosen=-1;mpz_class match1=0,match2=0;
  for(int k=0;k<64;k++){
   unsigned char seed[32];if(fread(seed,1,32,fi)!=32){cerr<<"EOF seed"<<endl;return 2;}
   ChaCha rng(seed);
   mpz_class b1=modr(rng.nextFieldRaw()*fr_mont_inverse);
   mpz_class b2=modr(rng.nextFieldRaw()*fr_mont_inverse);
   Pt blind=add(d0(b2),d1(b1));
   // compare to each known witness value
   for(int v=0;v<3;v++){
    if(eq(add(bases[v],blind),proofs[i].A)){
     found++;val=v;chosen=k;match1=b1;match2=b2;
    }
   }
  }
  output<<i<<" "<<val<<" "<<chosen<<" "<<found<<"\n";
  if(i<4 || i%25==0 || found!=1){cout<<"i="<<i<<" vote="<<val<<" guess="<<chosen<<" hits="<<found<<" sec="<<chrono::duration<double>(chrono::steady_clock::now()-start).count()<<endl;}
  if(i>=420 && i<=422) cout<<"Known populate vote "<<i<<" expected "<<((i-420)*2+1)%3<<" found "<<val<<endl;
 }
 fclose(fi);
}
