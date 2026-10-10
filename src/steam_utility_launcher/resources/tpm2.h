/*
 * Minimal TPM 2.0 client for the powershell.exe stand-in: just enough to give
 * ROTK Launcher's two TPM commands the same answers Windows' "Microsoft
 * Platform Crypto Provider" gives them, using this machine's REAL TPM through
 * the kernel resource manager (/dev/tpmrm0, seen from Wine as Z:\dev\tpmrm0).
 *
 * Nothing here ever fabricates a key: every key lives in the TPM (the files
 * on disk are only the TPM-wrapped blobs, useless on any other TPM), and if
 * the TPM can't be used the functions fail so the stand-in declines, exactly
 * as a PC without a TPM would.
 *
 * Both functions return 1 on success, writing a NUL-terminated answer to `out`,
 * and 0 on failure, writing the reason to `why`.
 */
#ifndef SUL_TPM2_H
#define SUL_TPM2_H

#include <stddef.h>
#include <stdint.h>

/* Level 1 (rotk-hwid-tpm-v1): signs `message` with a non-exportable ECDSA
 * P-256 key held by the TPM. Answer: base64(CNG ECC public blob) "|"
 * base64(64-byte r||s signature), the line the app parses. */
int tpm_level1_proof(const uint8_t *message, size_t length, char *out,
                     size_t out_cap, char *why, size_t why_cap);

/* Level 2 "anchor" (rotk-tpm-aik-v1): signs `message` with a restricted TPM
 * identity key and describes it. Answer: one JSON object with publicKey,
 * signature, tpmPublic (TPM2B_PUBLIC), ekPublicKey (CNG RSA blob),
 * manufacturer, version, firmware and ekCertificates (the ek-cert-N.der files
 * in the key directory, if any). */
int tpm_anchor_json(const uint8_t *message, size_t length, char *out,
                    size_t out_cap, char *why, size_t why_cap);

/* Credential activation for the identity key: `blob` is the server's
 * TPM2B_ID_OBJECT followed by TPM2B_ENCRYPTED_SECRET. The TPM recovers the
 * secret (using the EK, with a PolicySecret(endorsement) session); answer:
 * its base64. Fails if there is no identity key yet (it never creates one). */
int tpm_activate(const uint8_t *blob, size_t length, char *out, size_t out_cap,
                 char *why, size_t why_cap);

/* Creates the SRK, EK and both keys ahead of time and records the TPM maker
 * (manufacturer.txt) and EK public key (ek.pub) in the key directory, so the
 * host side can fetch the EK certificate chain into ek-cert-N.der files. */
int tpm_prepare(char *why, size_t why_cap);

#endif
