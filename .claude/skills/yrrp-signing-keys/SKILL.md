---
name: yrrp-signing-keys
description: Use when anything concerns the YRRP release signing keys or certificates — "where do the certs come from", "signing keys missing", "restore the keys", "keys lost after recreate", "MANIFEST.sha256 fails", "/opt/yrrp/signing is empty", "releasekey", "platform key", "APEX keys", "passwords file", "ANDROID_PW_FILE", "key backup", "Bitwarden", "the gpg archive", "rotate keys", "new certificate", "exit 67 from the entrypoint". Covers key origin, backup location, restore procedure, and ownership rules. NOT for running a signed build once keys are in place (yrrp-signed-ota-release).
---

# YRRP signing keys

Announce first: **Using yrrp-signing-keys to handle release keys.**

## Origin

`scripts/generate-signing-keys.sh` generated the set once, on 2026-10-03, on the builder: 11 APK/OTA keys and 75 APEX key sets, RSA-4096, encrypted PKCS#8, 86 X.509 certificates. Subject:

```text
/C=BE/ST=Vlaams Brabant/L=Leuven/O=Yim's Riced ROM Project/OU=Me, myself and AI/CN=Yim's Ricing Development Team
```

Every installed device trusts `releasekey`. Regenerating keys breaks OTA continuity and forces a clean install. Never regenerate without the user's explicit decision through `AskUserQuestion` (options: restore from backup / regenerate and accept clean install).

## Backup

- Encrypted archive: `android-signing-keys-20261003.tar.gz.gpg`, SHA-256 `50130731446d31a6165e74c1065fa318e9f41dad21dc72048b4956d60ef116ce`.
- Copies: Bitwarden attachment (primary), `/opt/android/.signing-export/` on the builder, `~/Downloads/` locally.
- The GPG passphrase is stored separately by the user. Never read, print, log, or ask for it in chat.

## Live location

`/opt/yrrp/signing` in the builder, bind-mounted from `/mnt/fast/docker/android/signing` (mode 0700, owner 950:950). The entrypoint validates ownership and exits 67 on mismatch; it never chowns the mount. Never `chown -R` anything that crosses it.

## Restore

Restore needs the passphrase, so the user runs it. Hand them this, to run with the `!` prefix:

```bash
! ssh -t AndroidBuilder 'set -e; test -z "$(find /opt/yrrp/signing -mindepth 1 -print -quit)"; gpg --pinentry-mode loopback --decrypt /opt/android/.signing-export/android-signing-keys-20261003.tar.gz.gpg | tar -xzf - --strip-components=1 -C /opt/yrrp/signing; cd /opt/yrrp/signing; sha256sum --check --quiet MANIFEST.sha256; test "$(readlink testkey.pk8)" = releasekey.pk8; test "$(readlink testkey.x509.pem)" = releasekey.x509.pem; echo "Signing keys restored and verified"'
```

If `/mnt/fast/docker/android/signing` does not exist on TrueNAS, the user creates it first:

```bash
! ssh truenas_admin@192.168.4.243 'sudo docker run --rm -v /mnt/fast/docker/android:/host alpine:3.22 sh -c "mkdir -p /host/signing && chown 950:950 /host/signing && chmod 0700 /host/signing"'
```

Then verify yourself: `ssh AndroidBuilder 'cd /opt/yrrp/signing && sha256sum --check --quiet MANIFEST.sha256 && echo ok'`.

## The passwords file

The archived `passwords` file still references the old path `/home/android/.android-certs`. Do not edit it; `MANIFEST.sha256` covers it. `sign-lineage-build.sh` writes a temporary rewritten copy pointing at `/opt/yrrp/signing` and removes it on exit.

`docs/build/signing.md` "Restore" still describes the old home-directory restore. The procedure above is current.
