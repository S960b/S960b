// virt (GlacierCTF) — native VM-assisted solver, Linux x86-64.
// Build: gcc -O2 -Wall -Wextra -o solve_virt solve_virt.c
// Run:   ./solve_virt ./virt/virt.exe
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define IMAGE_BASE ((uintptr_t)0x100000000ULL)
#define IMAGE_SIZE 0x2d000u
#define MAP_SIZE 0x30000u
#define VM_CHECK_RVA 0x12340u
#define VM_NEXT_RVA  0x12410u
#define TARGET_RVA   0x19860u
#define FLAG_LEN     40

static uint16_t u16(const unsigned char *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}
static uint32_t u32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static unsigned char *load_pe(const char *name) {
    FILE *f = fopen(name, "rb");
    if (!f) { perror(name); exit(EXIT_FAILURE); }
    if (fseek(f, 0, SEEK_END)) { perror("fseek"); exit(EXIT_FAILURE); }
    long size = ftell(f);
    if (size <= 0) { fprintf(stderr, "empty file\n"); exit(EXIT_FAILURE); }
    rewind(f);
    unsigned char *buf = malloc((size_t)size);
    if (!buf || fread(buf, 1, (size_t)size, f) != (size_t)size) {
        fprintf(stderr, "unable to read PE\n"); exit(EXIT_FAILURE);
    }
    fclose(f);

    if ((size_t)size < 0x100 || u16(buf) != 0x5a4d) {
        fprintf(stderr, "not an MZ executable\n"); exit(EXIT_FAILURE);
    }
    uint32_t pe = u32(buf + 0x3c);
    if ((size_t)pe + 24 > (size_t)size || u32(buf + pe) != 0x4550) {
        fprintf(stderr, "missing PE header\n"); exit(EXIT_FAILURE);
    }
    uint16_t nsect = u16(buf + pe + 6);
    uint16_t optsz = u16(buf + pe + 20);
    size_t opt = (size_t)pe + 24;
    size_t sh = opt + optsz;
    if (optsz < 64 || sh > (size_t)size || (size_t)nsect > ((size_t)size - sh)/40) {
        fprintf(stderr, "invalid section table\n"); exit(EXIT_FAILURE);
    }
    if (u16(buf + opt) != 0x20b || u32(buf + opt + 56) != IMAGE_SIZE) {
        fprintf(stderr, "unexpected PE32+ image layout\n"); exit(EXIT_FAILURE);
    }
    uint64_t image_base = (uint64_t)u32(buf + opt + 24) |
                          ((uint64_t)u32(buf + opt + 28) << 32);
    if (image_base != IMAGE_BASE) {
        fprintf(stderr, "unexpected PE ImageBase\n"); exit(EXIT_FAILURE);
    }

    // The image uses RIP-relative references, and self-modifying .frost0 code.
    // Allocate a private, executable, *writable* image at its preferred base.
    unsigned char *base = mmap((void *)IMAGE_BASE, MAP_SIZE,
        PROT_READ | PROT_WRITE | PROT_EXEC,
        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (base == MAP_FAILED) {
        perror("mmap fixed image base"); exit(EXIT_FAILURE);
    }

    for (unsigned i = 0; i < nsect; ++i) {
        const unsigned char *p = buf + sh + (size_t)i*40;
        uint32_t vsize = u32(p + 8);
        uint32_t rva = u32(p + 12);
        uint32_t rawsize = u32(p + 16);
        uint32_t rawptr = u32(p + 20);
        if ((uint64_t)rva + vsize > MAP_SIZE ||
            (rawsize && ((uint64_t)rawptr + rawsize > (size_t)size ||
                         (uint64_t)rva + rawsize > MAP_SIZE))) {
            fprintf(stderr, "section out of bounds\n"); exit(EXIT_FAILURE);
        }
        if (rawsize) memcpy(base + rva, buf + rawptr, rawsize);
    }
    free(buf);
    return base;
}

// PE entry functions follow the Windows x64 calling convention.
typedef uint32_t (__attribute__((ms_abi)) *vm_fn_t)(uint32_t, uint32_t, uint32_t);

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s path/to/virt.exe\n", argv[0]);
        return 2;
    }
    unsigned char *image = load_pe(argv[1]);
    vm_fn_t check = (vm_fn_t)(image + VM_CHECK_RVA);
    vm_fn_t next  = (vm_fn_t)(image + VM_NEXT_RVA);
    uint32_t state = FLAG_LEN ^ 0xdeadbeefu;
    char flag[FLAG_LEN + 1] = {0};

    for (unsigned i = 0; i < FLAG_LEN; ++i) {
        unsigned found = 0;
        uint32_t candidate = 0;
        uint8_t target = image[TARGET_RVA + i];
        for (uint32_t c = 32; c <= 126; ++c) {
            if (check(c, state, i) == target) {
                ++found;
                candidate = c;
            }
        }
        if (found != 1) {
            fprintf(stderr, "position %u: %u matches (state=%08x, target=%02x)\n",
                    i, found, state, target);
            return EXIT_FAILURE;
        }
        flag[i] = (char)candidate;
        state = next(candidate, state, i);
        printf("%2u  %c  state=%08" PRIx32 "\n", i, flag[i], state);
    }
    printf("FLAG: %s\n", flag);
    printf("final state: %08" PRIx32 "\n", state);
    if (memcmp(flag, "gctf{", 5) || flag[39] != '}') {
        fprintf(stderr, "recovered string has unexpected format\n");
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
