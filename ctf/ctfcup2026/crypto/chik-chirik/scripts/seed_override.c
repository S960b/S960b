#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <dlfcn.h>
static FILE *seedf=NULL;
static int opened=0;
static FILE *(*orig_fopen)(const char*,const char*);
static size_t (*orig_fread)(void*,size_t,size_t,FILE*);
static int (*orig_fclose)(FILE*);
static void ini(void){
  if(!orig_fopen) orig_fopen=dlsym(RTLD_NEXT,"fopen");
  if(!orig_fread) orig_fread=dlsym(RTLD_NEXT,"fread");
  if(!orig_fclose) orig_fclose=dlsym(RTLD_NEXT,"fclose");
}
static uint8_t hexdec(char a){if(a>='0'&&a<='9')return a-'0';if(a>='a'&&a<='f')return 10+a-'a';if(a>='A'&&a<='F')return 10+a-'A';return 0;}
FILE *fopen(const char* p,const char* m){
  ini();
  if(strcmp(p,"/dev/urandom"))return orig_fopen(p,m);
  FILE*f=orig_fopen("/dev/zero",m);
  if(opened++==0){seedf=f;}
  return f;
}
size_t fread(void *buf,size_t size,size_t n,FILE*f){
  ini();size_t got=orig_fread(buf,size,n,f);
  if(f==seedf){
    const char *hex=getenv("CHIRP_SEED_HEX");
    if(hex&&strlen(hex)==32&&size*got>=16){
       uint8_t *out=(uint8_t*)buf;
       for(int i=0;i<16;i++)out[i]=(hexdec(hex[i*2])<<4)|hexdec(hex[i*2+1]);
    }
  }
  return got;
}
int fclose(FILE *f){ini();if(f==seedf)seedf=NULL;return orig_fclose(f);}
