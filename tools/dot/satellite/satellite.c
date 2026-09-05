/*
 * echoneura-satellite — mic-to-WebSocket bridge for rooted devices.
 *
 * Built for the unlocked Amazon Echo Dot 2nd Gen (RS03QR / Fire OS 5, ARMv7),
 * but runs anywhere: it captures mono S16LE PCM (ALSA via vendored tinyalsa,
 * or a WAV file in --file mode) and streams it to EchoNeura's live-voice
 * endpoint (docs/device/voice-protocol.md), then prints the transcript and
 * assistant reply that come back on the same socket.
 *
 * Design constraints (the Dot has 256 MB RAM and Android 5.1's libc):
 *   - single static binary, no dependencies beyond libc + linux/sound headers
 *   - no TLS: ws:// only. For remote servers, tunnel (ssh -L / stunnel).
 *   - client frames are masked per RFC 6455; server frames are parsed leniently
 *
 * Build:  make native   (this machine, for testing)
 *         make cross    (static ARMv7 musl binary for the Dot)
 *
 * Usage on the Dot (after unlock, see docs/device/echo-dot-rs03qr-jailbreak.md):
 *   adb push satellite-arm /data/local/tmp/
 *   adb shell chmod 755 /data/local/tmp/satellite-arm
 *   adb shell /data/local/tmp/satellite-arm --host 192.168.1.20 --port 8000
 *   # Ctrl-C (or --seconds) ends the utterance and prints the reply
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include <tinyalsa/pcm.h>

#define CHUNK_SECONDS 0.25
#define RESULT_TIMEOUT_MS 30000

static volatile sig_atomic_t g_stop = 0;

static void on_sigint(int sig) { (void)sig; g_stop = 1; }

/* ------------------------------------------------------------------ utils */

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void die(const char *msg) {
    fprintf(stderr, "satellite: %s\n", msg);
    exit(1);
}

static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static void base64_encode(const uint8_t *in, size_t n, char *out) {
    size_t i, j = 0;
    for (i = 0; i + 2 < n; i += 3) {
        uint32_t v = (in[i] << 16) | (in[i + 1] << 8) | in[i + 2];
        out[j++] = B64[(v >> 18) & 63];
        out[j++] = B64[(v >> 12) & 63];
        out[j++] = B64[(v >> 6) & 63];
        out[j++] = B64[v & 63];
    }
    if (i < n) {
        uint32_t v = in[i] << 16;
        int pad = 1;
        if (i + 1 < n) {
            v |= in[i + 1] << 8;
            pad = 2;
        }
        out[j++] = B64[(v >> 18) & 63];
        out[j++] = B64[(v >> 12) & 63];
        out[j++] = pad == 2 ? B64[(v >> 6) & 63] : '=';
        out[j++] = '=';
    }
    out[j] = 0;
}

/* Pull a JSON string value for "key" out of a flat JSON blob (good enough
 * for our own server's single-line messages; handles \" escapes). */
static int json_str(const char *json, const char *key, char *out, size_t outsz) {
    char needle[64];
    snprintf(needle, sizeof needle, "\"%s\"", key);
    const char *p = strstr(json, needle);
    if (!p) return 0;
    p += strlen(needle);
    while (*p == ' ' || *p == ':' || *p == '\t') p++;
    if (*p != '"') return 0;
    p++;
    size_t j = 0;
    while (*p && *p != '"' && j + 1 < outsz) {
        if (*p == '\\' && p[1]) p++;
        out[j++] = *p++;
    }
    out[j] = 0;
    return 1;
}

/* ------------------------------------------------------------- websocket */

static int read_full(int fd, void *buf, size_t n) {
    uint8_t *p = buf;
    while (n) {
        ssize_t r = recv(fd, p, n, 0);
        if (r <= 0) return -1;
        p += r;
        n -= (size_t)r;
    }
    return 0;
}

static int read_file_fd(int fd, void *buf, size_t n) {
    uint8_t *p = buf;
    while (n) {
        ssize_t r = read(fd, p, n);
        if (r <= 0) return -1;
        p += r;
        n -= (size_t)r;
    }
    return 0;
}

static int write_full(int fd, const void *buf, size_t n) {
    const uint8_t *p = buf;
    while (n) {
        ssize_t r = send(fd, p, n, MSG_NOSIGNAL);
        if (r <= 0) return -1;
        p += r;
        n -= (size_t)r;
    }
    return 0;
}

static int ws_connect(const char *host, const char *port, const char *path_query) {
    struct addrinfo hints = {0}, *res = NULL;
    struct sockaddr_in sin = {0};
    struct sockaddr *sa;
    socklen_t salen;
    int in_port = atoi(port);

    /* IP literals bypass getaddrinfo: keeps statically-linked libc builds
     * (musl is fine, static glibc has no NSS) working with --host 1.2.3.4. */
    if (inet_pton(AF_INET, host, &sin.sin_addr) == 1) {
        sin.sin_family = AF_INET;
        sin.sin_port = htons((uint16_t)in_port);
        sa = (struct sockaddr *)&sin;
        salen = sizeof sin;
    } else {
        hints.ai_family = AF_UNSPEC;
        hints.ai_socktype = SOCK_STREAM;
        if (getaddrinfo(host, port, &hints, &res) != 0 || !res) die("cannot resolve host");
        sa = res->ai_addr;
        salen = res->ai_addrlen;
    }
    int fd = socket(sa->sa_family, SOCK_STREAM, 0);
    if (fd < 0) die("socket() failed");
    if (connect(fd, sa, salen) != 0) die("connect() failed (is the API running?)");
    if (res) freeaddrinfo(res);
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);

    uint8_t raw[16];
    int rfd = open("/dev/urandom", O_RDONLY);
    if (rfd < 0 || read_file_fd(rfd, raw, sizeof raw) != 0) die("no entropy");
    close(rfd);
    char key[32];
    base64_encode(raw, sizeof raw, key);

    char req[1024];
    int n = snprintf(req, sizeof req,
                     "GET %s HTTP/1.1\r\n"
                     "Host: %s:%s\r\n"
                     "Upgrade: websocket\r\n"
                     "Connection: Upgrade\r\n"
                     "Sec-WebSocket-Key: %s\r\n"
                     "Sec-WebSocket-Version: 13\r\n\r\n",
                     path_query, host, port, key);
    if (write_full(fd, req, (size_t)n) != 0) die("handshake send failed");

    char resp[4096];
    size_t got = 0;
    while (got < sizeof resp - 1) {
        ssize_t r = recv(fd, resp + got, 1, 0);
        if (r <= 0) die("handshake: connection closed");
        got += (size_t)r;
        if (got >= 4 && memcmp(resp + got - 4, "\r\n\r\n", 4) == 0) break;
    }
    resp[got] = 0;
    if (!strstr(resp, "101")) die("handshake rejected (no HTTP 101)");
    return fd;
}

/* client -> server frames are always masked (RFC 6455 §5.3) */
static int ws_send(int fd, uint8_t opcode, const uint8_t *payload, size_t len) {
    uint8_t hdr[14];
    size_t h = 0;
    hdr[h++] = 0x80 | opcode;
    if (len < 126) {
        hdr[h++] = 0x80 | (uint8_t)len;
    } else if (len < 65536) {
        hdr[h++] = 0x80 | 126;
        hdr[h++] = (uint8_t)(len >> 8);
        hdr[h++] = (uint8_t)(len & 0xff);
    } else {
        hdr[h++] = 0x80 | 127;
        for (int i = 7; i >= 0; i--) hdr[h++] = (uint8_t)((uint64_t)len >> (8 * i)) & 0xff;
    }
    uint8_t mask[4];
    int rfd = open("/dev/urandom", O_RDONLY);
    if (rfd < 0 || read_file_fd(rfd, mask, 4) != 0) return -1;
    close(rfd);
    memcpy(hdr + h, mask, 4);
    h += 4;
    if (write_full(fd, hdr, h) != 0) return -1;

    uint8_t *masked = malloc(len ? len : 1);
    if (!masked) return -1;
    for (size_t i = 0; i < len; i++) masked[i] = payload[i] ^ mask[i & 3];
    int rc = write_full(fd, masked, len);
    free(masked);
    return rc;
}

/* returns opcode, or -1 on close/error; payload is NUL-terminated for text */
static int ws_recv(int fd, uint8_t **payload, size_t *len) {
    uint8_t hdr[2];
    if (read_full(fd, hdr, 2) != 0) return -1;
    uint8_t op = hdr[0] & 0x0f;
    int masked = hdr[1] & 0x80;
    uint64_t n = hdr[1] & 0x7f;
    if (n == 126) {
        uint8_t e[2];
        if (read_full(fd, e, 2) != 0) return -1;
        n = (e[0] << 8) | e[1];
    } else if (n == 127) {
        uint8_t e[8];
        if (read_full(fd, e, 8) != 0) return -1;
        n = 0;
        for (int i = 0; i < 8; i++) n = (n << 8) | e[i];
    }
    uint8_t mask[4] = {0};
    if (masked && read_full(fd, mask, 4) != 0) return -1;
    uint8_t *buf = malloc((size_t)n + 1);
    if (!buf) return -1;
    if (n && read_full(fd, buf, (size_t)n) != 0) {
        free(buf);
        return -1;
    }
    for (uint64_t i = 0; i < n; i++) buf[i] ^= mask[i & 3];
    buf[n] = 0;
    *payload = buf;
    *len = (size_t)n;
    return op;
}

static void ws_send_text(int fd, const char *text) {
    ws_send(fd, 0x1, (const uint8_t *)text, strlen(text));
}

/* ---------------------------------------------------------------- sources */

typedef struct {
    const uint8_t *data;
    size_t size;
    unsigned rate;
} wav_t;

static int wav_load(const char *path, wav_t *w) {
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    uint8_t hdr[12];
    if (fread(hdr, 1, 12, f) != 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) {
        fclose(f);
        return -1;
    }
    w->rate = 16000;
    unsigned channels = 1, bits = 16;
    w->data = NULL;
    for (;;) {
        uint8_t ch[8];
        if (fread(ch, 1, 8, f) != 8) break;
        uint32_t sz = ch[4] | (ch[5] << 8) | (ch[6] << 16) | ((uint32_t)ch[7] << 24);
        if (memcmp(ch, "fmt ", 4) == 0) {
            uint8_t fmt[16];
            if (fread(fmt, 1, 16, f) != 16) break;
            channels = fmt[2] | (fmt[3] << 8);
            w->rate = fmt[4] | (fmt[5] << 8) | (fmt[6] << 16) | ((uint32_t)fmt[7] << 24);
            bits = fmt[14] | (fmt[15] << 8);
            if (sz > 16) fseek(f, sz - 16, SEEK_CUR);
        } else if (memcmp(ch, "data", 4) == 0) {
            w->size = sz;
            uint8_t *buf = malloc(sz);
            if (!buf || fread(buf, 1, sz, f) != sz) {
                free(buf);
                break;
            }
            w->data = buf;
            break;
        } else {
            fseek(f, sz + (sz & 1), SEEK_CUR);
        }
    }
    fclose(f);
    if (!w->data) return -1;
    if (channels != 1 || bits != 16) {
        fprintf(stderr, "satellite: need mono 16-bit WAV (ffmpeg -i in -ac 1 -ar 16000 -f wav out.wav)\n");
        return -1;
    }
    return 0;
}

/* ------------------------------------------------------------------ main */

int main(int argc, char **argv) {
    const char *host = "127.0.0.1", *port = "8000", *file = NULL, *source = "dot";
    unsigned rate = 16000, card = 0, dev = 0;
    double seconds = 0; /* 0 = until Ctrl-C in mic mode */
    int fast = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--host") && i + 1 < argc) host = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = argv[++i];
        else if (!strcmp(argv[i], "--file") && i + 1 < argc) file = argv[++i];
        else if (!strcmp(argv[i], "--source") && i + 1 < argc) source = argv[++i];
        else if (!strcmp(argv[i], "--rate") && i + 1 < argc) rate = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--card") && i + 1 < argc) card = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--device") && i + 1 < argc) dev = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atof(argv[++i]);
        else if (!strcmp(argv[i], "--fast")) fast = 1;
        else {
            fprintf(stderr,
                    "usage: satellite --host H --port P [--file x.wav | --card N --device N]\n"
                    "                 [--rate 16000] [--seconds N] [--source dot] [--fast]\n");
            return 2;
        }
    }

    signal(SIGINT, on_sigint);
    signal(SIGTERM, on_sigint);

    wav_t wav = {0};
    struct pcm *pcm = NULL;
    size_t chunk = (size_t)((double)rate * 2 * CHUNK_SECONDS);
    uint8_t *buf = malloc(chunk);
    if (!buf) die("oom");

    if (file) {
        if (wav_load(file, &wav) != 0) die("cannot read WAV");
        rate = wav.rate;
        chunk = (size_t)((double)rate * 2 * CHUNK_SECONDS);
        buf = realloc(buf, chunk);
        fprintf(stderr, "satellite: file %s (%u Hz, %.1fs)\n", file, rate, (double)wav.size / (rate * 2));
    } else {
        struct pcm_config cfg = {
            .channels = 1,
            .rate = rate,
            .period_size = 1024,
            .period_count = 4,
            .format = PCM_FORMAT_S16_LE,
            .start_threshold = 1,
            .stop_threshold = 0,
            .silence_threshold = 0,
        };
        pcm = pcm_open(card, dev, PCM_IN, &cfg);
        if (!pcm || !pcm_is_ready(pcm)) die("pcm_open failed (mic busy? try --card/--device; is Alexa disabled?)");
        fprintf(stderr, "satellite: capturing card %u device %u @ %u Hz (Ctrl-C to send)\n", card, dev, rate);
    }

    char pq[512];
    snprintf(pq, sizeof pq,
             "/api/voice/stream?fmt=pcm_s16le&sample_rate=%u&language=auto&source=%s", rate, source);
    int fd = ws_connect(host, port, pq);

    /* drain the server's ready message */
    uint8_t *msg = NULL;
    size_t mlen = 0;
    int op = ws_recv(fd, &msg, &mlen);
    if (op == 0x1) fprintf(stderr, "satellite: <- %s\n", msg);
    free(msg);

    double t0 = now_s();
    size_t off = 0;
    double sent_s = 0;
    int rc = 0;

    while (!g_stop && (seconds <= 0 || sent_s < seconds)) {
        if (file) {
            if (off >= wav.size) break;
            size_t n = wav.size - off < chunk ? wav.size - off : chunk;
            memcpy(buf, wav.data + off, n);
            off += n;
        } else {
            if (pcm_readi(pcm, buf, (unsigned int)(chunk / 2)) != 0) {
                fprintf(stderr, "satellite: pcm_readi: %s\n", pcm_get_error(pcm));
                rc = 1;
                break;
            }
        }
        if (ws_send(fd, 0x2, buf, chunk) != 0) die("send failed");
        sent_s += (double)chunk / (rate * 2);
        if (file && !fast) {
            double wait = t0 + sent_s - now_s();
            if (wait > 0) {
                struct timespec ts = {(time_t)wait, (long)((wait - (time_t)wait) * 1e9)};
                nanosleep(&ts, NULL);
            }
        }
    }

    fprintf(stderr, "satellite: sent %.2fs, asking server to process…\n", sent_s);
    ws_send_text(fd, "{\"type\":\"stop\"}");

    struct timeval tv = {RESULT_TIMEOUT_MS / 1000, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

    for (;;) {
        uint8_t *payload = NULL;
        size_t len = 0;
        op = ws_recv(fd, &payload, &len);
        if (op < 0) break;
        if (op == 0x9) {
            ws_send(fd, 0xA, payload, len);
            free(payload);
            continue;
        }
        if (op == 0x8) {
            free(payload);
            break;
        }
        if (op == 0x1) {
            const char *text = (const char *)payload;
            if (strstr(text, "\"result\"")) {
                char tr[2048] = {0}, reply[2048] = {0}, action[64] = {0};
                json_str(text, "transcript", tr, sizeof tr);
                json_str(text, "reply", reply, sizeof reply);
                json_str(text, "action", action, sizeof action);
                printf("--- transcript ---\n%s\n--- assistant [%s] ---\n%s\n", tr, action, reply);
                free(payload);
                break;
            }
            if (strstr(text, "\"discarded\"") || strstr(text, "\"error\"")) {
                fprintf(stderr, "satellite: <- %s\n", text);
                rc = 2;
                free(payload);
                break;
            }
            fprintf(stderr, "satellite: <- %s\n", text);
        }
        free(payload);
    }

    ws_send(fd, 0x8, NULL, 0);
    close(fd);
    if (pcm) pcm_close(pcm);
    free(buf);
    free((void *)wav.data);
    return rc;
}
