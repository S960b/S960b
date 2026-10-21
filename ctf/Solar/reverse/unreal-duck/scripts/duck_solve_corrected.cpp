#include <cstdio>
#include <cstdint>
#include <array>
#include <cstring>
static inline uint32_t rotr(uint32_t x,int k){return (x>>k)|(x<<(32-k));}
struct R {uint32_t a,b,c,d,e,f;};
const R rec[3] = {
 {0x6f37aa0b,0x112bb232,0x29b1711a,0x45d39389,0xb2dcbf59,0x6f4b4eda},
 {0x17d98198,0x42413596,0x73257244,0x57d52250,0x2523ab39,0x203585fc},
 {0x2f48611f,0x39c19d54,0x158b4d73,0x583a99d9,0xb01e4f4d,0x2b74299a}
};
struct State{ uint32_t R,E,S; };
uint32_t symbol(char c, uint32_t key=0x2f6cb09a){
 uint32_t v=(uint32_t(1 - int(c))*27u+(key^0xffffffe5u))&63u;
 return (((v>>3)^v)*13+17)&63u;
}
inline State step(State q,uint32_t v){
 uint32_t R=((q.R<<6)|v)&0x3fffffffu;
 uint32_t S=rotr(((v+1)*0x54e31f9bu)^q.S,25)+0x2c8b91efu;
 uint32_t E=rotr((S^R)+0x7b642d11u,23)^(R*0x1f5d3b79u);
 return {R,E,S};
}
static inline uint32_t calc(const State &q,int i){
 const auto &p=rec[i];
 uint32_t t=rotr(q.E+p.b,21)^rotr(p.f+q.R,27);
 t+=p.d^q.S;
 t^=p.a;
 t=rotr(t,25);
 t+=rotr(p.b,29)^p.f;
 t^=rotr(q.R+0x36d5e22bu+p.d,19);
 return t^p.e;
}
int main(int argc,char**argv){
 uint32_t seq[26];for(int i=0;i<26;++i)seq[i]=symbol('A'+i);
 for(int i=0;i<26;++i)printf("%c=%u%s",'A'+i,seq[i],i==25?"\n":" ");
 State check={0,0,0};for(char k : "JJJJJ") if(k) check=step(check,symbol(k));printf("JJJJJ route=[%08x %08x %08x] state=[%08x %08x %08x]\n",calc(check,0),calc(check,1),calc(check,2),check.R,check.E,check.S);
 char text[6];text[5]=0;uint64_t trials=0;uint64_t hits[3]={};
 for(int a=0;a<26;a++){State x0=step({0,0,0},seq[a]);text[0]='A'+a;
 for(int route=0;route<3;route++)if(calc(x0,route)==0)printf("FOUND len1 route=%d pwd=%c\n",route,text[0]);
 for(int b=0;b<26;b++){State x1=step(x0,seq[b]);text[1]='A'+b;
 for(int route=0;route<3;route++)if(calc(x1,route)==0)printf("FOUND len2 route=%d pwd=%.2s\n",route,text);
 for(int c=0;c<26;c++){State x2=step(x1,seq[c]);text[2]='A'+c;
 for(int route=0;route<3;route++)if(calc(x2,route)==0)printf("FOUND len3 route=%d pwd=%.3s\n",route,text);
 for(int d=0;d<26;d++){State x3=step(x2,seq[d]);text[3]='A'+d;
 for(int route=0;route<3;route++)if(calc(x3,route)==0)printf("FOUND len4 route=%d pwd=%.4s\n",route,text);
 for(int e=0;e<26;e++){State x4=step(x3,seq[e]);text[4]='A'+e;
  ++trials;
  for(int route=0;route<3;route++){if(calc(x4,route)==0){hits[route]++;printf("FOUND route=%d password=%s R=%08x E=%08x S=%08x\n",route,text,x4.R,x4.E,x4.S);}}
 }}}}
 }
 printf("trials=%llu hits=[%llu,%llu,%llu]\n",(unsigned long long)trials,(unsigned long long)hits[0],(unsigned long long)hits[1],(unsigned long long)hits[2]);
}
