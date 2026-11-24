# Oracle Always Free (v34)
Use only Always Free resources in the account home region. Do not upgrade to paid.
Shape VM.Standard.A1.Flex, Ubuntu ARM64, 2 OCPU, 12 GB RAM, 150 GB boot volume (within 200 GB total, including other volumes).
Capacity is not guaranteed; idle VMs may be reclaimed. Keep independent backups.
From Android use an SSH client, key authentication, and local port forwarding to 127.0.0.1:8765 (verify dashboard port in unit). Never expose dashboard directly.
Extract the archive on the VM. Run `bash streamclipper_os/deploy/install.sh --plan` before installation. Existing installer requires a fresh Debian/Ubuntu system.
After installation verify `uname -m`, and run the venv Python with `import ctranslate2; print(ctranslate2.get_supported_compute_types('cpu'))` and `import faster_whisper`. ARM dependencies and actual model inference must be verified on the VM.
The service remains preview-only, one worker, with CPU quota and memory ceiling. New recordings are blocked below 20 GiB free. No files are automatically deleted: retention policy requires review of publication and job state. Do not enable real recording or uploads before live validation and rights checks.
