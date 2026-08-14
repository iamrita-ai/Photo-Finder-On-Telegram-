"""
Turns a Pinterest HLS (.m3u8) video stream into a local .mp4 file using
ffmpeg, so it can actually be uploaded/played on Telegram (Telegram's
send_video does not accept HLS playlist URLs directly).

Uses `-c copy` (stream copy, no re-encoding) so it's fast and doesn't need
much CPU — it just repackages the existing H.264/AAC streams into an mp4
container.
"""
import asyncio
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


async def remux_hls_to_mp4(url: str, timeout: int = 90) -> str | None:
    """Download + remux an HLS stream to a local mp4 file. Returns the file
    path on success, or None on failure/timeout. Caller must delete the
    file afterwards (see cleanup())."""
    fd, path = tempfile.mkstemp(suffix=".mp4", prefix="pin_")
    os.close(fd)

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        logger.error("ffmpeg not found — is it installed in the container? (see Dockerfile)")
        cleanup(path)
        return None

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("ffmpeg remux timed out (%ss) for %s", timeout, url)
        cleanup(path)
        return None

    if proc.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) == 0:
        logger.warning(
            "ffmpeg remux failed (rc=%s) for %s: %s",
            proc.returncode,
            url,
            (stderr or b"").decode(errors="ignore")[:500],
        )
        cleanup(path)
        return None

    return path


def cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
