#include <gmpxx.h>
#include <iostream>
#include <fstream>
#include <string>
#include <vector>
using namespace std;
const mpz_class MOD("89025939082434796413600671938588077849057880765442485138379977529032945827841");
const mpz_class ORDER("1358428025549847357385264159219178433970057909696144578443833691290271744");
const mpz_class Q=ORDER/65536;
struct Pt {mpz_class x,y,z;};
Pt zero() {return {0,1,0};}
Pt aff(const mpz_class &x,const mpz_class &y) {return {x,y,1};}
mpz_class red(const mpz_class&a){mpz_class r;r=a%MOD;if(r<0)r+=MOD;return r;}
Pt dbl(const Pt &p){if(p.z==0 || p.y==0)return zero();
  auto &x=p.x,&y=p.y,&z=p.z;
  mpz_class xx=red(x*x),yy=red(y*y),yyyy=red(yy*yy), zz=red(z*z);
  mpz_class s=red(2*(red((x+yy)*(x+yy))-xx-yyyy));
  mpz_class m=red(3*xx+red(zz*zz));
  mpz_class x3=red(m*m-2*s), y3=red(m*(s-x3)-8*yyyy),z3=red(2*y*z);
  return {move(x3),move(y3),move(z3)};
}
Pt add(const Pt&l,const Pt&r) {
 if(l.z==0)return r;if(r.z==0)return l;
 auto &x1=l.x,&y1=l.y,&z1=l.z, &x2=r.x,&y2=r.y,&z2=r.z;
 mpz_class z1z1=red(z1*z1),z2z2=red(z2*z2),u1=red(x1*z2z2),u2=red(x2*z1z1);
 mpz_class s1=red(y1*z2*z2z2),s2=red(y2*z1*z1z1),h=red(u2-u1),t=red(s2-s1);
 if(h==0)return t==0?dbl(l):zero();
 mpz_class hh=red(h*h),hhh=red(h*hh),v=red(u1*hh);
 mpz_class x3=red(t*t-hhh-2*v),y3=red(t*(v-x3)-s1*hhh),z3=red(z1*z2*h);
 return {move(x3),move(y3),move(z3)};
}
Pt neg(Pt p){if(p.z!=0 && p.y!=0)p.y=MOD-p.y;return p;}
Pt mul(mpz_class k,Pt p){Pt r=zero();while(k>0){if(mpz_odd_p(k.get_mpz_t()))r=add(r,p);p=dbl(p);k>>=1;}return r;}
Pt mul2(int k,Pt r) {while(k-->0)r=dbl(r);return r;}
bool same(const Pt& a,const Pt& b) {if(a.z==0||b.z==0)return a.z==0&&b.z==0;
  mpz_class x1=red(a.x*b.z*b.z),x2=red(b.x*a.z*a.z);
  if(x1!=x2)return false;
  return red(a.y*b.z*b.z*b.z)==red(b.y*a.z*a.z*a.z);
}
int main(int argc,char**argv){Pt g=aff(mpz_class("41209836325234781044181597304544742925948820810888367894489840040559411336265"),mpz_class("58717619821656938994285449686691658631929085725700003126366067459102233551806"));
  Pt H=mul(Q,g);vector<Pt> hpow(16);hpow[0]=H;for(int i=1;i<16;i++)hpow[i]=dbl(hpow[i-1]);Pt half=hpow[15];
  cerr<<"H order 65536: "<< (mul2(16,H).z==0) <<" order>32768 "<<(half.z!=0)<<endl;
  ifstream f(argv[1]);ofstream o(argv[2]);
  int i,port;string sx,sy;int n=0;
  while(f>>i>>port>>sx>>sy){Pt s=aff(mpz_class(sx),mpz_class(sy));Pt t=mul(Q,s);unsigned bits=0;
   for(int k=0;k<16;k++){
    Pt test=mul2(15-k,t);
    if(same(test,half)){bits|=1u<<k;t=add(t,neg(hpow[k]));}
    else if(test.z!=0){cerr<<"Invalid at "<<i<<" k="<<k<<endl;return 1;}
   }
   if(t.z!=0){cerr<<"Nonzero residual "<<i<<endl;return 2;}
   o<<i<<" "<<bits<<"\n";n++;
   if(n%200==0)cerr<<"processed "<<n<<endl;
  }
  cerr<<"total="<<n<<endl;
}
