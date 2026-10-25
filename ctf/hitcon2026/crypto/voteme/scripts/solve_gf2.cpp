#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <vector>
#include <chrono>
#include <cstring>
#include <algorithm>
#include <iostream>
#include <fstream>
using namespace std;
const int WIDTH=8;
static inline bool bit(const uint64_t*row,int i){return (row[i>>6]>>(i&63))&1ULL;}
static inline void xorrow(uint64_t*a,const uint64_t*b,int len){
 // hints for SIMD compiler
 #pragma GCC ivdep
 for(int j=0;j<len;j++)a[j]^=b[j];
}
static inline int extract(const uint64_t*row,int i,int w){ // aligned >= 0, potentially crossing
 int pos=i>>6, sh=i&63;
 uint64_t z=row[pos]>>sh;
 if(sh+w>64)z|=row[pos+1]<<(64-sh);
 return z&((1<<w)-1);
}
int main(int argc,char**argv){FILE*fi=fopen(argv[1],"rb");uint32_t dims[3];fread(dims,4,3,fi);int n=dims[0],m=dims[1],bytes=dims[2];int W=(n+1+63)/64;
 cout<<"bits="<<n<<" equations="<<m<<" stride="<<W<<" bytes/row="<<bytes<<endl;
 auto st=chrono::steady_clock::now();
 vector<uint64_t> storage((size_t)m*W,0);
 for(int i=0;i<m;i++)fread((char*)&storage[(size_t)i*W],1,bytes,fi);
 fclose(fi);
 vector<uint64_t*> rows(m);for(int i=0;i<m;i++) rows[i]=storage.data()+(size_t)i*W;
 vector<uint64_t> lookup((1<<WIDTH)*W);
 int rank=0; vector<int> pivcols; pivcols.reserve(n);
 for(int block=0;block<n;block+=WIDTH){
   int width=min(WIDTH,n-block);
   int bases[WIDTH]={0};uint64_t*bp[WIDTH]={};
   for(int j=0;j<width;j++){
     int found=-1;
     for(int i=rank;i<m;i++){
       int mask=extract(rows[i],block,width);
       for(int k=0;k<j;k++)if((mask&(1<<k)) && bases[k])mask^=bases[k];
       if(mask & (1<<j)){found=i;break;}
     }
     if(found<0)continue; // free variable: some initial MT state bits cannot affect captured outputs
     swap(rows[rank],rows[found]);
     uint64_t *pivot=rows[rank];
     for(int k=0;k<j;k++)if(bases[k] && bit(pivot,block+k))xorrow(pivot,bp[k],W);
     bases[j]=extract(pivot,block,width); bp[j]=pivot;
     if(!(bases[j]&(1<<j))){cerr<<"bad pivot"<<endl;return 1;}
     pivcols.push_back(block+j);rank++;
   }
   for(int mask=0;mask<(1<<width);mask++){
     uint64_t *tbl=lookup.data()+(size_t)mask*W;
     memset(tbl,0,W*8);
     int rem=mask;
     for(int k=0;k<width;k++)if((rem&(1<<k))&&bases[k]){
       rem^=bases[k];xorrow(tbl,bp[k],W);
     }
     // Remaining columns with no pivot could not be eliminated; such masks are not in the row span.
   }
   for(int i=rank;i<m;i++){
     int mask=extract(rows[i],block,width);
     if(mask)xorrow(rows[i],lookup.data()+(size_t)mask*W,W);
     if(extract(rows[i],block,width)){
       cerr<<"unexpected residual in block "<<block<<" row "<<i<<endl;return 5;
     }
   }
   if(block%2048==0){double sec=chrono::duration<double>(chrono::steady_clock::now()-st).count();
     cout<<"col="<<block<<" rank="<<rank<<" elapsed="<<sec<<" sec"<<endl;}
 }
 cout<<"rank="<<rank<<" total="<<n<<" runtime="<<chrono::duration<double>(chrono::steady_clock::now()-st).count()<<" sec"<<endl;
 int contradictions=0;
 for(int i=rank;i<m;i++) if(bit(rows[i],n))contradictions++;
 cout<<"contradictions="<<contradictions<<endl;
 if(contradictions)return 3;
 vector<uint64_t> x(W,0);
 for(int i=rank-1;i>=0;i--){
   uint64_t parity=0;uint64_t*row=rows[i];
   for(int j=pivcols[i]>>6;j<W;j++)parity^=(uint64_t)__builtin_parityll(row[j]&x[j]);
   int b=(int)(parity&1)^bit(row,n);
   if(b)x[pivcols[i]>>6]|=1ULL<<(pivcols[i]&63);
 }
 // Validate all original equations by regenerating from file
 fi=fopen(argv[1],"rb");fread(dims,4,3,fi);
 vector<uint64_t> tmp(W,0);int mism=0;
 for(int i=0;i<m;i++){
  fill(tmp.begin(),tmp.end(),0);fread(tmp.data(),1,bytes,fi);
  int v=0;for(int j=0;j<W;j++)v^=__builtin_parityll(tmp[j]&x[j]);
  if((v^bit(tmp.data(),n))!=0)mism++;
 }
 fclose(fi);
 cout<<"verification mismatches="<<mism<<endl;
 ofstream f(argv[2]);for(int i=0;i<n;i++)f<<((x[i>>6]>>(i&63))&1);f<<'\n';f.close();
 return mism?4:0;
}
