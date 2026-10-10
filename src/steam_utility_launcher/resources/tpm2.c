/* See tpm2.h. Command layouts are from the TCG TPM 2.0 Library spec, part 3. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "tpm2.h"

#define DEVICE_PATH L"Z:\\dev\\tpmrm0"
#define TPM_TIMEOUT_MS 30000
#define RC_FAIL 0xFFFFFFFFu

#define ST_NO_SESSIONS 0x8001
#define ST_SESSIONS 0x8002
#define ALG_RSA 0x0001
#define ALG_SHA256 0x000B
#define ALG_AES 0x0006
#define ALG_CFB 0x0043
#define ALG_NULL 0x0010
#define ALG_ECDSA 0x0018
#define ALG_ECC 0x0023
#define ECC_NIST_P256 0x0003
#define RH_OWNER 0x40000001u
#define RH_NULL 0x40000007u
#define RS_PW 0x40000009u
#define RH_ENDORSEMENT 0x4000000Bu

#define CC_HASH_SEQUENCE_COMPLETE 0x13E
#define CC_CREATE_PRIMARY 0x131
#define CC_CREATE 0x153
#define CC_LOAD 0x157
#define CC_SEQUENCE_UPDATE 0x15C
#define CC_SIGN 0x15D
#define CC_FLUSH_CONTEXT 0x165
#define CC_GET_CAPABILITY 0x17A
#define CC_HASH 0x17D
#define CC_HASH_SEQUENCE_START 0x186

#define MAX_HASH_CHUNK 1024
#define KEY_ATTR_BASE 0x00000472u /* fixedTPM|fixedParent|sensitiveDataOrigin|userWithAuth|noDA */
#define KEY_ATTR_SIGN 0x00040000u
#define KEY_ATTR_RESTRICTED 0x00010000u

#define HWID_KEY_FILE L"rotk-hwid-tpm-v1.key"
#define AIK_KEY_FILE L"rotk-tpm-aik-v1.key"
#define EK_CACHE_FILE L"ek.pub"
#define KEY_BLOB_MAX 2048

/* --------------------------------------------------------------- SHA-256 */

static const uint32_t K256[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_block(uint32_t h[8], const uint8_t *p) {
    uint32_t w[64], a, b, c, d, e, f, g, hh;
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    a = h[0]; b = h[1]; c = h[2]; d = h[3]; e = h[4]; f = h[5]; g = h[6]; hh = h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = hh + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
        uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

static void sha256(const uint8_t *data, size_t n, uint8_t out[32]) {
    uint32_t h[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    uint8_t tail[128];
    size_t full = n / 64, rest = n % 64, tail_len = rest < 56 ? 64 : 128;
    uint64_t bits = (uint64_t)n * 8;
    for (size_t i = 0; i < full; i++) sha256_block(h, data + 64 * i);
    memset(tail, 0, sizeof tail);
    memcpy(tail, data + 64 * full, rest);
    tail[rest] = 0x80;
    for (int i = 0; i < 8; i++) tail[tail_len - 1 - i] = (uint8_t)(bits >> (8 * i));
    sha256_block(h, tail);
    if (tail_len == 128) sha256_block(h, tail + 64);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = (uint8_t)(h[i] >> 24); out[4 * i + 1] = (uint8_t)(h[i] >> 16);
        out[4 * i + 2] = (uint8_t)(h[i] >> 8); out[4 * i + 3] = (uint8_t)h[i];
    }
}

/* ---------------------------------------------------------------- base64 */

static void b64_encode(const uint8_t *in, size_t n, char *out) {
    static const char T[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t o = 0;
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = (uint32_t)in[i] << 16 | (i + 1 < n ? (uint32_t)in[i + 1] << 8 : 0) | (i + 2 < n ? in[i + 2] : 0);
        out[o++] = T[v >> 18 & 63];
        out[o++] = T[v >> 12 & 63];
        out[o++] = i + 1 < n ? T[v >> 6 & 63] : '=';
        out[o++] = i + 2 < n ? T[v & 63] : '=';
    }
    out[o] = 0;
}

/* ------------------------------------------------------------- the wire */

typedef struct { uint8_t b[8192]; size_t n; } wire;

static HANDLE g_device = INVALID_HANDLE_VALUE;

static void w8(wire *w, uint8_t v) { w->b[w->n++] = v; }
static void w16(wire *w, uint16_t v) { w8(w, (uint8_t)(v >> 8)); w8(w, (uint8_t)v); }
static void w32(wire *w, uint32_t v) { w16(w, (uint16_t)(v >> 16)); w16(w, (uint16_t)v); }
static void wbytes(wire *w, const uint8_t *p, size_t n) { memcpy(w->b + w->n, p, n); w->n += n; }
static void w2b(wire *w, const uint8_t *p, size_t n) { w16(w, (uint16_t)n); wbytes(w, p, n); }
static uint16_t r16(const uint8_t *p) { return (uint16_t)(p[0] << 8 | p[1]); }
static uint32_t r32(const uint8_t *p) { return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]; }

static void start(wire *w, uint16_t tag, uint32_t code) { w->n = 0; w16(w, tag); w32(w, 0); w32(w, code); }
static void password_auth(wire *w) { w32(w, 9); w32(w, RS_PW); w16(w, 0); w8(w, 0); w16(w, 0); }

static void failure(char *why, size_t cap, const char *what, uint32_t rc) {
    if (why && cap) snprintf(why, cap, "%s (TPM response 0x%x)", what, rc);
}

/* One overlapped transfer, waited for up to the timeout. Wine opens the device
 * non-blocking, and the kernel's TPM device answers a read that arrives before
 * the response is ready with an empty read (sporadically, depending on timing)
 * instead of waiting. Overlapped I/O has Wine wait for readiness itself, so
 * there is nothing to poll. Returns the byte count, or -1 with `why` set. */
static long transfer(int writing, void *buf, DWORD length, char *why, size_t cap) {
    OVERLAPPED ov;
    DWORD done = 0;
    BOOL ok;
    memset(&ov, 0, sizeof ov);
    ov.hEvent = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!ov.hEvent) { if (why && cap) snprintf(why, cap, "no event for TPM I/O (error %lu)", GetLastError()); return -1; }
    ok = writing ? WriteFile(g_device, buf, length, &done, &ov) : ReadFile(g_device, buf, length, &done, &ov);
    if (!ok && GetLastError() == ERROR_IO_PENDING) {
        if (WaitForSingleObject(ov.hEvent, TPM_TIMEOUT_MS) != WAIT_OBJECT_0) {
            CancelIo(g_device);
            GetOverlappedResult(g_device, &ov, &done, TRUE);   /* `ov` must not be used after it's gone */
            CloseHandle(ov.hEvent);
            if (why && cap) snprintf(why, cap, "the TPM did not answer within %d s", TPM_TIMEOUT_MS / 1000);
            return -1;
        }
        ok = GetOverlappedResult(g_device, &ov, &done, FALSE);
    }
    CloseHandle(ov.hEvent);
    if (!ok) {
        if (why && cap) snprintf(why, cap, "%s the TPM failed (error %lu)", writing ? "writing to" : "reading from", GetLastError());
        return -1;
    }
    return (long)done;
}

/* Sends the command and waits for the answer; returns the TPM response code,
 * or RC_FAIL if the exchange itself failed. */
static uint32_t exchange(wire *cmd, wire *rsp, char *why, size_t cap) {
    long wrote, got;
    cmd->b[2] = (uint8_t)(cmd->n >> 24); cmd->b[3] = (uint8_t)(cmd->n >> 16);
    cmd->b[4] = (uint8_t)(cmd->n >> 8); cmd->b[5] = (uint8_t)cmd->n;
    wrote = transfer(1, cmd->b, (DWORD)cmd->n, why, cap);
    if (wrote < 0) return RC_FAIL;
    if ((size_t)wrote != cmd->n) { if (why && cap) snprintf(why, cap, "the TPM took only %ld of %zu bytes", wrote, cmd->n); return RC_FAIL; }
    got = transfer(0, rsp->b, (DWORD)sizeof rsp->b, why, cap);
    if (got < 0) return RC_FAIL;
    if (got < 10) { if (why && cap) snprintf(why, cap, "the TPM gave a short answer (%ld bytes)", got); return RC_FAIL; }
    rsp->n = (size_t)got;
    return r32(rsp->b + 6);
}

static int open_device(char *why, size_t cap) {
    g_device = CreateFileW(DEVICE_PATH, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, NULL);
    if (g_device == INVALID_HANDLE_VALUE) {
        if (why && cap) snprintf(why, cap, "no usable TPM at /dev/tpmrm0 (error %lu)", GetLastError());
        return 0;
    }
    return 1;
}

static void close_device(void) {
    if (g_device != INVALID_HANDLE_VALUE) CloseHandle(g_device);
    g_device = INVALID_HANDLE_VALUE;
}

/* ------------------------------------------------------------- TPM commands */

static void flush_handle(uint32_t handle) {
    wire cmd, rsp;
    start(&cmd, ST_NO_SESSIONS, CC_FLUSH_CONTEXT);
    w32(&cmd, handle);
    (void)exchange(&cmd, &rsp, NULL, 0);
}

/* Appends an empty TPM2B_SENSITIVE_CREATE. */
static void empty_sensitive(wire *w) { w16(w, 4); w16(w, 0); w16(w, 0); }

/* Appends a TPM2B_PUBLIC whose body the caller builds between begin/end. */
static size_t begin_public(wire *w) { w16(w, 0); return w->n; }
static void end_public(wire *w, size_t body) {
    uint16_t size = (uint16_t)(w->n - body);
    w->b[body - 2] = (uint8_t)(size >> 8);
    w->b[body - 1] = (uint8_t)size;
}

/* The owner-hierarchy storage root: an ECC P-256 primary, recreated from its
 * template every time (the same template always yields the same key), so key
 * blobs created under it stay loadable across processes. */
static int create_srk(uint32_t *handle, char *why, size_t cap) {
    wire cmd, rsp;
    size_t body;
    uint32_t rc;
    start(&cmd, ST_SESSIONS, CC_CREATE_PRIMARY);
    w32(&cmd, RH_OWNER);
    password_auth(&cmd);
    empty_sensitive(&cmd);
    body = begin_public(&cmd);
    w16(&cmd, ALG_ECC); w16(&cmd, ALG_SHA256); w32(&cmd, 0x00030472);
    w16(&cmd, 0);
    w16(&cmd, ALG_AES); w16(&cmd, 128); w16(&cmd, ALG_CFB);
    w16(&cmd, ALG_NULL); w16(&cmd, ECC_NIST_P256); w16(&cmd, ALG_NULL);
    w16(&cmd, 0); w16(&cmd, 0);
    end_public(&cmd, body);
    w16(&cmd, 0); w32(&cmd, 0);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "creating the storage key failed", rc); return 0; }
    *handle = r32(rsp.b + 10);
    return 1;
}

/* The standard TCG RSA-2048 endorsement key; copies its TPM2B_PUBLIC (with the
 * size prefix) into `pub`. Takes a second or two, so callers cache the result. */
static int create_ek(uint8_t *pub, size_t *pub_len, uint32_t *keep, char *why, size_t cap) {
    static const uint8_t policy[32] = {
        0x83,0x71,0x97,0x67,0x44,0x84,0xb3,0xf8,0x1a,0x90,0xcc,0x8d,0x46,0xa5,0xd7,0x24,
        0xfd,0x52,0xd7,0x6e,0x06,0x52,0x0b,0x64,0xf2,0xa1,0xda,0x1b,0x33,0x14,0x69,0xaa};
    static const uint8_t zeros[256] = {0};
    wire cmd, rsp;
    size_t body;
    uint32_t rc, handle;
    size_t n;
    start(&cmd, ST_SESSIONS, CC_CREATE_PRIMARY);
    w32(&cmd, RH_ENDORSEMENT);
    password_auth(&cmd);
    empty_sensitive(&cmd);
    body = begin_public(&cmd);
    w16(&cmd, ALG_RSA); w16(&cmd, ALG_SHA256); w32(&cmd, 0x000300B2);
    w2b(&cmd, policy, sizeof policy);
    w16(&cmd, ALG_AES); w16(&cmd, 128); w16(&cmd, ALG_CFB);
    w16(&cmd, ALG_NULL); w16(&cmd, 2048); w32(&cmd, 0);
    w2b(&cmd, zeros, sizeof zeros);
    end_public(&cmd, body);
    w16(&cmd, 0); w32(&cmd, 0);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "creating the endorsement key failed", rc); return 0; }
    handle = r32(rsp.b + 10);
    n = 2 + r16(rsp.b + 18);                       /* outPublic follows the 4-byte parameterSize */
    memcpy(pub, rsp.b + 18, n);
    *pub_len = n;
    if (keep) *keep = handle; else flush_handle(handle);
    return 1;
}

/* A child of `parent`: an ECDSA P-256 signing key. `restricted` makes it the
 * attestation-style identity key (it then signs only digests the TPM itself
 * produced). Copies TPM2B_PRIVATE then TPM2B_PUBLIC into `blob`. */
static int create_signing_key(uint32_t parent, int restricted, uint8_t *blob, size_t *blob_len,
                              char *why, size_t cap) {
    wire cmd, rsp;
    size_t body, off, priv, pub;
    uint32_t rc;
    start(&cmd, ST_SESSIONS, CC_CREATE);
    w32(&cmd, parent);
    password_auth(&cmd);
    empty_sensitive(&cmd);
    body = begin_public(&cmd);
    w16(&cmd, ALG_ECC); w16(&cmd, ALG_SHA256);
    w32(&cmd, KEY_ATTR_BASE | KEY_ATTR_SIGN | (restricted ? KEY_ATTR_RESTRICTED : 0));
    w16(&cmd, 0);
    w16(&cmd, ALG_NULL);
    w16(&cmd, ALG_ECDSA); w16(&cmd, ALG_SHA256);
    w16(&cmd, ECC_NIST_P256); w16(&cmd, ALG_NULL);
    w16(&cmd, 0); w16(&cmd, 0);
    end_public(&cmd, body);
    w16(&cmd, 0); w32(&cmd, 0);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "creating the signing key failed", rc); return 0; }
    off = 14;                                       /* after the parameterSize */
    priv = 2 + r16(rsp.b + off);
    pub = 2 + r16(rsp.b + off + priv);
    if (priv + pub > KEY_BLOB_MAX) { if (why && cap) snprintf(why, cap, "unexpectedly large key"); return 0; }
    memcpy(blob, rsp.b + off, priv + pub);
    *blob_len = priv + pub;
    return 1;
}

static int load_key(uint32_t parent, const uint8_t *blob, size_t blob_len, uint32_t *handle,
                    char *why, size_t cap) {
    wire cmd, rsp;
    uint32_t rc;
    start(&cmd, ST_SESSIONS, CC_LOAD);
    w32(&cmd, parent);
    password_auth(&cmd);
    wbytes(&cmd, blob, blob_len);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "loading the key failed", rc); return 0; }
    *handle = r32(rsp.b + 10);
    return 1;
}

/* The TPM's own SHA-256 of `data` plus the ticket a restricted key requires,
 * for any length (long inputs go through a hash sequence). `ticket` receives
 * the TPMT_TK_HASHCHECK bytes. */
static int tpm_hash(const uint8_t *data, size_t n, uint8_t digest[32], uint8_t ticket[48],
                    size_t *ticket_len, char *why, size_t cap) {
    wire cmd, rsp;
    uint32_t rc;
    const uint8_t *tail;
    if (n <= MAX_HASH_CHUNK) {
        start(&cmd, ST_NO_SESSIONS, CC_HASH);
        w2b(&cmd, data, n);
        w16(&cmd, ALG_SHA256); w32(&cmd, RH_OWNER);
        rc = exchange(&cmd, &rsp, why, cap);
        if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "hashing in the TPM failed", rc); return 0; }
        tail = rsp.b + 10;                           /* outHash, then validation */
    } else {
        uint32_t seq;
        start(&cmd, ST_NO_SESSIONS, CC_HASH_SEQUENCE_START);
        w16(&cmd, 0); w16(&cmd, ALG_SHA256);
        rc = exchange(&cmd, &rsp, why, cap);
        if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "starting a TPM hash failed", rc); return 0; }
        seq = r32(rsp.b + 10);
        while (n > MAX_HASH_CHUNK) {
            start(&cmd, ST_SESSIONS, CC_SEQUENCE_UPDATE);
            w32(&cmd, seq); password_auth(&cmd);
            w2b(&cmd, data, MAX_HASH_CHUNK);
            rc = exchange(&cmd, &rsp, why, cap);
            if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "extending a TPM hash failed", rc); flush_handle(seq); return 0; }
            data += MAX_HASH_CHUNK; n -= MAX_HASH_CHUNK;
        }
        start(&cmd, ST_SESSIONS, CC_HASH_SEQUENCE_COMPLETE);
        w32(&cmd, seq); password_auth(&cmd);
        w2b(&cmd, data, n);
        w32(&cmd, RH_OWNER);
        rc = exchange(&cmd, &rsp, why, cap);
        if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "finishing a TPM hash failed", rc); flush_handle(seq); return 0; }
        tail = rsp.b + 14;                           /* after the parameterSize */
    }
    if (r16(tail) != 32) { if (why && cap) snprintf(why, cap, "unexpected TPM digest size"); return 0; }
    memcpy(digest, tail + 2, 32);
    *ticket_len = 2 + 4 + 2 + r16(tail + 2 + 32 + 2 + 4);
    if (*ticket_len > 48) { if (why && cap) snprintf(why, cap, "unexpected TPM ticket"); return 0; }
    memcpy(ticket, tail + 2 + 32, *ticket_len);
    return 1;
}

/* ECDSA-SHA256 over `digest`; `ticket` is NULL for an unrestricted key.
 * Writes the 64-byte r||s signature. */
static int tpm_sign(uint32_t key, const uint8_t digest[32], const uint8_t *ticket, size_t ticket_len,
                    uint8_t signature[64], char *why, size_t cap) {
    static const uint8_t null_ticket[8] = {0x80, 0x24, 0x40, 0x00, 0x00, 0x07, 0x00, 0x00};
    wire cmd, rsp;
    uint32_t rc;
    const uint8_t *p;
    size_t r_len, s_len;
    start(&cmd, ST_SESSIONS, CC_SIGN);
    w32(&cmd, key);
    password_auth(&cmd);
    w2b(&cmd, digest, 32);
    w16(&cmd, ALG_NULL);                             /* use the key's own scheme */
    if (ticket) wbytes(&cmd, ticket, ticket_len); else wbytes(&cmd, null_ticket, sizeof null_ticket);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "signing in the TPM failed", rc); return 0; }
    p = rsp.b + 14;                                  /* TPMT_SIGNATURE: alg, hash, R, S */
    if (r16(p) != ALG_ECDSA) { if (why && cap) snprintf(why, cap, "unexpected signature type"); return 0; }
    p += 4;
    r_len = r16(p); p += 2;
    if (r_len > 32) { if (why && cap) snprintf(why, cap, "unexpected signature"); return 0; }
    memset(signature, 0, 64);
    memcpy(signature + 32 - r_len, p, r_len); p += r_len;
    s_len = r16(p); p += 2;
    if (s_len > 32) { if (why && cap) snprintf(why, cap, "unexpected signature"); return 0; }
    memcpy(signature + 64 - s_len, p, s_len);
    return 1;
}

/* TPM_PT_FIXED properties we report: manufacturer, firmware, spec family. */
static int read_tpm_info(uint32_t *manufacturer, uint32_t *fw1, uint32_t *fw2, char *why, size_t cap) {
    wire cmd, rsp;
    uint32_t rc, count;
    start(&cmd, ST_NO_SESSIONS, CC_GET_CAPABILITY);
    w32(&cmd, 6); w32(&cmd, 0x100); w32(&cmd, 64);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "reading the TPM's properties failed", rc); return 0; }
    count = r32(rsp.b + 15);
    *manufacturer = *fw1 = *fw2 = 0;
    for (uint32_t i = 0; i < count && 19 + 8 * (i + 1) <= rsp.n; i++) {
        uint32_t property = r32(rsp.b + 19 + 8 * i), value = r32(rsp.b + 23 + 8 * i);
        if (property == 0x105) *manufacturer = value;
        else if (property == 0x10B) *fw1 = value;
        else if (property == 0x10C) *fw2 = value;
    }
    return 1;
}

/* ------------------------------------------------------------ persistence */

static void key_directory(wchar_t *dir) {
    DWORD n = GetEnvironmentVariableW(L"SUL_TPM_DIR", dir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) {
        wchar_t local[MAX_PATH];
        n = GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
        if (n == 0 || n >= MAX_PATH) wcscpy(local, L"C:\\users\\steamuser\\AppData\\Local");
        swprintf(dir, MAX_PATH, L"%ls\\sul-tpm", local);
    }
    for (wchar_t *c = dir + 3; *c; c++) {            /* create each level after "X:\" */
        if (*c == L'\\') { *c = 0; CreateDirectoryW(dir, NULL); *c = L'\\'; }
    }
    CreateDirectoryW(dir, NULL);
}

static void file_path(wchar_t *out, const wchar_t *name) {
    wchar_t dir[MAX_PATH];
    key_directory(dir);
    swprintf(out, MAX_PATH * 2, L"%ls\\%ls", dir, name);
}

static int read_file(const wchar_t *path, uint8_t *buf, size_t cap, size_t *n) {
    DWORD got = 0;
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!ReadFile(h, buf, (DWORD)cap, &got, NULL) || got == 0) { CloseHandle(h); return 0; }
    CloseHandle(h);
    *n = got;
    return 1;
}

static int write_file(const wchar_t *path, const uint8_t *data, size_t n) {
    wchar_t tmp[MAX_PATH * 2 + 16];
    DWORD wrote = 0;
    HANDLE h;
    swprintf(tmp, sizeof tmp / sizeof tmp[0], L"%ls.%lu.tmp", path, GetCurrentProcessId());
    h = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!WriteFile(h, data, (DWORD)n, &wrote, NULL) || wrote != (DWORD)n) { CloseHandle(h); DeleteFileW(tmp); return 0; }
    CloseHandle(h);
    if (!MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING)) { DeleteFileW(tmp); return 0; }
    return 1;
}

/* Loads the named key under `srk`, creating it (once) if there is none or the
 * saved one no longer loads (for example the TPM was cleared). Copies the
 * key's TPM2B_PUBLIC to `pub`. */
static int ensure_key(uint32_t srk, const wchar_t *file, int restricted, uint32_t *handle,
                      uint8_t *pub, size_t *pub_len, char *why, size_t cap) {
    uint8_t blob[KEY_BLOB_MAX];
    size_t blob_len = 0, priv;
    wchar_t path[MAX_PATH * 2];
    file_path(path, file);
    if (read_file(path, blob, sizeof blob, &blob_len) && blob_len > 4) {
        priv = 2 + r16(blob);
        if (priv + 2 <= blob_len && priv + 2 + r16(blob + priv) == blob_len &&
            load_key(srk, blob, blob_len, handle, NULL, 0))
            goto have;
    }
    if (!create_signing_key(srk, restricted, blob, &blob_len, why, cap)) return 0;
    if (!load_key(srk, blob, blob_len, handle, why, cap)) return 0;
    if (!write_file(path, blob, blob_len)) {
        flush_handle(*handle);
        if (why && cap) snprintf(why, cap, "could not save the key blob (SUL_TPM_DIR not writable?)");
        return 0;
    }
have:
    priv = 2 + r16(blob);
    *pub_len = 2 + r16(blob + priv);
    memcpy(pub, blob + priv, *pub_len);
    return 1;
}

/* ------------------------------------------------------ Windows-shaped blobs */

/* CNG ECC public blob (BCRYPT_ECCKEY_BLOB, "ECS1") from a TPM2B_PUBLIC that
 * ends with the point: x then y, each a 2-byte size followed by 32 bytes. */
static int ecc_blob(const uint8_t *tpm_public, size_t n, uint8_t blob[72]) {
    const uint8_t *point = tpm_public + n - 68;
    if (n < 2 + 68 || r16(point) != 32 || r16(point + 34) != 32) return 0;
    memcpy(blob, "ECS1", 4);
    blob[4] = 32; blob[5] = blob[6] = blob[7] = 0;
    memcpy(blob + 8, point + 2, 32);
    memcpy(blob + 40, point + 36, 32);
    return 1;
}

/* CNG RSA public blob (BCRYPT_RSAKEY_BLOB, "RSA1") from the EK's TPM2B_PUBLIC. */
static int rsa_blob(const uint8_t *tpm_public, size_t n, uint8_t *blob, size_t *blob_len) {
    const uint8_t *p = tpm_public + 2;                 /* skip the size */
    uint32_t exponent;
    uint16_t bits, modulus;
    if (n < 2 + 2 + 2 + 4 || r16(p) != ALG_RSA) return 0;
    p += 2 + 2 + 4;                                    /* type, nameAlg, attributes */
    p += 2 + r16(p);                                   /* authPolicy */
    p += 6;                                            /* symmetric: alg, keyBits, mode */
    p += 2;                                            /* scheme */
    bits = r16(p); p += 2;
    exponent = r32(p); p += 4;
    modulus = r16(p); p += 2;
    if (bits != 2048 || modulus != 256 || (size_t)(p + modulus - tpm_public) > n) return 0;
    if (exponent == 0) exponent = 65537;
    memcpy(blob, "RSA1", 4);
    blob[4] = (uint8_t)(bits & 0xff); blob[5] = (uint8_t)(bits >> 8); blob[6] = blob[7] = 0;
    blob[8] = 3; blob[9] = blob[10] = blob[11] = 0;                              /* cbPublicExp */
    blob[12] = (uint8_t)(modulus & 0xff); blob[13] = (uint8_t)(modulus >> 8); blob[14] = blob[15] = 0;
    memset(blob + 16, 0, 8);                                                     /* no primes */
    blob[24] = (uint8_t)(exponent >> 16); blob[25] = (uint8_t)(exponent >> 8); blob[26] = (uint8_t)exponent;
    memcpy(blob + 27, p, modulus);
    *blob_len = 27 + modulus;
    return 1;
}

/* ------------------------------------------------------ EK, maker, certificates */

/* The EK never changes: create it once, then reuse the cached public part. */
static int ensure_ek(uint8_t *ek, size_t *ek_len, char *why, size_t cap) {
    wchar_t path[MAX_PATH * 2];
    file_path(path, EK_CACHE_FILE);
    if (read_file(path, ek, KEY_BLOB_MAX, ek_len) && *ek_len > 2 && (size_t)(2 + r16(ek)) == *ek_len) return 1;
    if (!create_ek(ek, ek_len, NULL, why, cap)) return 0;
    write_file(path, ek, *ek_len);
    return 1;
}

/* The four ASCII letters of the TPM manufacturer, e.g. "AMD". */
static void manufacturer_text(uint32_t manufacturer, char maker[8]) {
    for (int i = 0; i < 4; i++) {
        char c = (char)(manufacturer >> (24 - 8 * i));
        maker[i] = (c >= 0x20 && c < 0x7f && c != '"' && c != '\\') ? c : 0;
    }
    maker[4] = 0;
    for (int i = 3; i >= 0 && (maker[i] == 0 || maker[i] == ' '); i--) maker[i] = 0;
}

#define CERT_FILE_COUNT 4
#define CERT_MAX 3072

/* The `ekCertificates` array: the DER certificates the launcher's host side
 * saved as ek-cert-1..4.der (EK certificate first, then its issuers) in the
 * key directory, each as base64. Empty when there are none. */
static int tpm_requested(void) {
    wchar_t flag[4];
    return GetEnvironmentVariableW(L"SUL_TPM", flag, 4) > 0 && flag[0] == L'1';
}

static int endorsement_enabled(void) {
    wchar_t flag[4];
    return GetEnvironmentVariableW(L"SUL_TPM_ENDORSEMENT", flag, 4) > 0 && flag[0] == L'1';
}

static void certificates_json(char *out, size_t cap) {
    static uint8_t der[CERT_MAX];
    size_t used = 0, n;
    out[0] = 0;
    if (!endorsement_enabled()) return;             /* opt-in only */
    for (int i = 1; i <= CERT_FILE_COUNT; i++) {
        wchar_t name[32], path[MAX_PATH * 2];
        char text[4 * (CERT_MAX / 3 + 1) + 8];
        swprintf(name, sizeof name / sizeof name[0], L"ek-cert-%d.der", i);
        file_path(path, name);
        if (!read_file(path, der, sizeof der, &n)) break;
        b64_encode(der, n, text);
        if (used + strlen(text) + 4 >= cap) break;
        used += (size_t)snprintf(out + used, cap - used, "%s\"%s\"", used ? "," : "", text);
    }
}

/* ------------------------------------------------------------ public API */

int tpm_level1_proof(const uint8_t *message, size_t length, char *out, size_t out_cap, char *why,
                     size_t why_cap) {
    uint32_t srk = 0, key = 0;
    uint8_t pub[KEY_BLOB_MAX], digest[32], signature[64], blob[72];
    size_t pub_len = 0;
    char pub64[128], sig64[128];
    int ok = 0;
    if (!open_device(why, why_cap)) return 0;
    if (!create_srk(&srk, why, why_cap)) goto done;
    if (!ensure_key(srk, HWID_KEY_FILE, 0, &key, pub, &pub_len, why, why_cap)) goto done;
    sha256(message, length, digest);
    if (!tpm_sign(key, digest, NULL, 0, signature, why, why_cap)) goto done;
    if (!ecc_blob(pub, pub_len, blob)) { snprintf(why, why_cap, "unexpected key description"); goto done; }
    b64_encode(blob, sizeof blob, pub64);
    b64_encode(signature, sizeof signature, sig64);
    if (strlen(pub64) + strlen(sig64) + 2 > out_cap) { snprintf(why, why_cap, "output too large"); goto done; }
    snprintf(out, out_cap, "%s|%s", pub64, sig64);
    ok = 1;
done:
    if (key) flush_handle(key);
    if (srk) flush_handle(srk);
    close_device();
    return ok;
}

int tpm_anchor_json(const uint8_t *message, size_t length, char *out, size_t out_cap, char *why,
                    size_t why_cap) {
    uint32_t srk = 0, key = 0, manufacturer, fw1, fw2;
    uint8_t pub[KEY_BLOB_MAX], ek[KEY_BLOB_MAX], digest[32], ticket[48], signature[64];
    uint8_t ecc[72], rsa[400];
    size_t pub_len = 0, ticket_len = 0, ek_len = 0, rsa_len = 0;
    char pub64[128], sig64[128], tpm64[2 * KEY_BLOB_MAX], ek64[600], maker[8];
    static char certs[16384];
    int ok = 0;
    if (!open_device(why, why_cap)) return 0;
    if (!create_srk(&srk, why, why_cap)) goto done;
    if (!ensure_key(srk, AIK_KEY_FILE, 1, &key, pub, &pub_len, why, why_cap)) goto done;
    if (!tpm_hash(message, length, digest, ticket, &ticket_len, why, why_cap)) goto done;
    if (!tpm_sign(key, digest, ticket, ticket_len, signature, why, why_cap)) goto done;

    if (!ensure_ek(ek, &ek_len, why, why_cap)) goto done;
    if (!ecc_blob(pub, pub_len, ecc) || !rsa_blob(ek, ek_len, rsa, &rsa_len)) {
        snprintf(why, why_cap, "unexpected key description");
        goto done;
    }
    if (!read_tpm_info(&manufacturer, &fw1, &fw2, why, why_cap)) goto done;
    manufacturer_text(manufacturer, maker);

    certificates_json(certs, sizeof certs);
    b64_encode(ecc, sizeof ecc, pub64);
    b64_encode(signature, sizeof signature, sig64);
    b64_encode(pub, pub_len, tpm64);
    b64_encode(rsa, rsa_len, ek64);
    if (snprintf(out, out_cap,
                 "{\"publicKey\":\"%s\",\"signature\":\"%s\",\"tpmPublic\":\"%s\",\"ekPublicKey\":\"%s\","
                 "\"manufacturer\":\"%s\",\"version\":\"2.0\",\"firmware\":\"%08x%08x\",\"ekCertificates\":[%s]}",
                 pub64, sig64, tpm64, ek64, maker, fw1, fw2, certs) >= (int)out_cap) {
        snprintf(why, why_cap, "output too large");
        goto done;
    }
    ok = 1;
done:
    if (key) flush_handle(key);
    if (srk) flush_handle(srk);
    close_device();
    return ok;
}

#define CC_ACTIVATE_CREDENTIAL 0x147
#define CC_POLICY_SECRET 0x151
#define CC_START_AUTH_SESSION 0x176
#define SE_POLICY 0x01
#define NONCE_SIZE 32
#define ACTIVATION_MAX 1024
#define SECRET_MAX 64

/* Starts a policy session satisfied by PolicySecret(endorsement hierarchy):
 * the authorization the standard EK demands. */
static int endorsement_session(uint32_t *session, char *why, size_t cap) {
    wire cmd, rsp;
    uint8_t nonce[NONCE_SIZE];
    uint32_t rc;
    if (BCryptGenRandom(NULL, nonce, sizeof nonce, BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
        if (why && cap) snprintf(why, cap, "no random numbers for the policy session");
        return 0;
    }
    start(&cmd, ST_NO_SESSIONS, CC_START_AUTH_SESSION);
    w32(&cmd, RH_NULL); w32(&cmd, RH_NULL);
    w2b(&cmd, nonce, sizeof nonce);
    w16(&cmd, 0);                                    /* no encrypted salt */
    w8(&cmd, SE_POLICY);
    w16(&cmd, ALG_NULL);                             /* no parameter encryption */
    w16(&cmd, ALG_SHA256);
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) { if (rc != RC_FAIL) failure(why, cap, "starting the policy session failed", rc); return 0; }
    *session = r32(rsp.b + 10);

    start(&cmd, ST_SESSIONS, CC_POLICY_SECRET);
    w32(&cmd, RH_ENDORSEMENT); w32(&cmd, *session);
    password_auth(&cmd);
    w16(&cmd, 0); w16(&cmd, 0); w16(&cmd, 0);        /* nonceTPM, cpHash, policyRef */
    w32(&cmd, 0);                                    /* no expiration */
    rc = exchange(&cmd, &rsp, why, cap);
    if (rc != 0) {
        if (rc != RC_FAIL) failure(why, cap, "the endorsement policy was refused", rc);
        flush_handle(*session);
        return 0;
    }
    return 1;
}

int tpm_activate(const uint8_t *blob, size_t length, char *out, size_t out_cap, char *why,
                 size_t why_cap) {
    uint32_t srk = 0, aik = 0, ek = 0, session = 0, rc;
    uint8_t key[KEY_BLOB_MAX], ek_pub[KEY_BLOB_MAX];
    size_t key_len = 0, ek_len = 0, id_size, secret_size;
    wchar_t path[MAX_PATH * 2];
    wire cmd, rsp;
    int ok = 0;
    if (!endorsement_enabled() || !tpm_requested()) { snprintf(why, why_cap, "endorsement extras are not enabled (--tpm-endorsement)"); return 0; }
    /* TPM2B_ID_OBJECT followed by TPM2B_ENCRYPTED_SECRET, nothing else. */
    if (length < 4 || length > ACTIVATION_MAX) { snprintf(why, why_cap, "unexpected credential blob size"); return 0; }
    id_size = r16(blob);
    if (2 + id_size + 2 > length) { snprintf(why, why_cap, "malformed credential blob"); return 0; }
    secret_size = r16(blob + 2 + id_size);
    if (2 + id_size + 2 + secret_size != length) { snprintf(why, why_cap, "malformed credential blob"); return 0; }

    if (!open_device(why, why_cap)) return 0;
    if (!create_srk(&srk, why, why_cap)) goto done;
    file_path(path, AIK_KEY_FILE);                   /* the identity key must already exist */
    if (!read_file(path, key, sizeof key, &key_len) || !load_key(srk, key, key_len, &aik, why, why_cap)) {
        if (!aik) snprintf(why, why_cap, "no identity key to activate a credential for");
        goto done;
    }
    if (!create_ek(ek_pub, &ek_len, &ek, why, why_cap)) goto done;
    if (!endorsement_session(&session, why, why_cap)) goto done;

    start(&cmd, ST_SESSIONS, CC_ACTIVATE_CREDENTIAL);
    w32(&cmd, aik); w32(&cmd, ek);
    w32(&cmd, 9 + 9);                                /* two authorizations: the key's password, the EK's policy */
    w32(&cmd, RS_PW); w16(&cmd, 0); w8(&cmd, 0); w16(&cmd, 0);
    w32(&cmd, session); w16(&cmd, 0); w8(&cmd, 0); w16(&cmd, 0);
    wbytes(&cmd, blob, length);
    rc = exchange(&cmd, &rsp, why, why_cap);
    session = 0;                                     /* a session without continueSession ends with the command */
    if (rc != 0) { if (rc != RC_FAIL) failure(why, why_cap, "the TPM refused to activate the credential", rc); goto done; }
    {
        size_t n = rsp.n >= 16 ? r16(rsp.b + 14) : 0;
        if (n == 0 || n > SECRET_MAX || 16 + n > rsp.n) { snprintf(why, why_cap, "unexpected credential size"); goto done; }
        if (4 * ((n + 2) / 3) + 1 > out_cap) { snprintf(why, why_cap, "output too large"); goto done; }
        b64_encode(rsp.b + 16, n, out);
    }
    ok = 1;
done:
    if (session) flush_handle(session);
    if (ek) flush_handle(ek);
    if (aik) flush_handle(aik);
    if (srk) flush_handle(srk);
    close_device();
    return ok;
}

/* Creates the SRK and EK and both keys ahead of time (so the app's own calls
 * stay quick) and reports the maker, so the host side can fetch the EK
 * certificate. Writes the EK public key to ek.pub and the maker to
 * manufacturer.txt in the key directory. */
int tpm_prepare(char *why, size_t why_cap) {
    uint32_t srk = 0, key = 0, manufacturer, fw1, fw2;
    uint8_t pub[KEY_BLOB_MAX], ek[KEY_BLOB_MAX];
    size_t pub_len = 0, ek_len = 0;
    char maker[8];
    wchar_t path[MAX_PATH * 2];
    int ok = 0;
    if (!open_device(why, why_cap)) return 0;
    if (!create_srk(&srk, why, why_cap)) goto done;
    if (!ensure_key(srk, HWID_KEY_FILE, 0, &key, pub, &pub_len, why, why_cap)) goto done;
    flush_handle(key);
    key = 0;
    if (!ensure_key(srk, AIK_KEY_FILE, 1, &key, pub, &pub_len, why, why_cap)) goto done;
    if (!ensure_ek(ek, &ek_len, why, why_cap)) goto done;
    if (!read_tpm_info(&manufacturer, &fw1, &fw2, why, why_cap)) goto done;
    manufacturer_text(manufacturer, maker);
    file_path(path, L"manufacturer.txt");
    write_file(path, (const uint8_t *)maker, strlen(maker));
    ok = 1;
done:
    if (key) flush_handle(key);
    if (srk) flush_handle(srk);
    close_device();
    return ok;
}
