"""Where replayed agents run: a Docker container with no network, no secrets and hard limits.

The agent loop itself runs on the host (it needs the model API and its key); only the agent's
tool calls happen in the container, against a throwaway copy of the old tree. File reads and
writes go through the container, never host-side, so a symlink the agent plants cannot make the
host read or write anything outside the copy.
"""

import io
import os
import posixpath
import shlex
import tarfile
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

DEFAULT_IMAGE = "python:3.12-slim"
WORKDIR = "/work"
MAX_OUTPUT_CHARS = 4000
MAX_READ_BYTES = 20_000
KILLED_EXIT_CODES = {124, 137}  # timeout(1) and SIGKILL


class SandboxError(Exception):
    pass


class ExecResult(BaseModel):
    exit_code: int
    output: str
    timed_out: bool = False


class Sandbox(Protocol):
    def exec(self, command: str, timeout: int) -> ExecResult: ...

    def read_file(self, path: str) -> str: ...

    def write_file(self, path: str, content: str) -> None: ...

    def close(self) -> None: ...


def safe_relpath(path: str) -> str:
    """A repo-relative POSIX path, or SandboxError. No absolute paths, no `..`, no NULs."""
    norm = posixpath.normpath(path.replace("\\", "/"))
    if not path or "\x00" in path or norm.startswith(("/", "..")) or norm == ".":
        raise SandboxError(f"path must be relative and inside the repository: {path!r}")
    return norm


class DockerSandbox:
    """One container per run. `host_dir` is a temp copy mounted read-write at /work."""

    def __init__(
        self,
        host_dir: Path,
        image: str = DEFAULT_IMAGE,
        memory: str = "1g",
        cpus: float = 1.0,
        pids: int = 256,
        client: Any = None,
    ) -> None:
        import docker

        self._client = client or docker.from_env()
        self._ensure_image(image)
        getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
        user = f"{getuid()}:{getgid()}" if getuid and getgid else None  # POSIX hosts only
        self._container = self._client.containers.run(
            image,
            ["sleep", "86400"],
            detach=True,
            network_mode="none",  # no network at all: nothing to exfiltrate to or download
            mem_limit=memory,
            nano_cpus=int(cpus * 1e9),
            pids_limit=pids,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            volumes={str(host_dir): {"bind": WORKDIR, "mode": "rw"}},
            working_dir=WORKDIR,
            environment={},  # no host environment, so no secrets
            user=user,
            labels={"rulewright": "prove"},
        )

    def _ensure_image(self, image: str) -> None:
        import docker

        try:
            self._client.images.get(image)
        except docker.errors.ImageNotFound:
            self._client.images.pull(image)  # the host pulls; the container has no network

    def exec(self, command: str, timeout: int) -> ExecResult:
        code, out = self._container.exec_run(
            ["timeout", "-s", "KILL", str(timeout), "sh", "-c", command], workdir=WORKDIR
        )
        text = out.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        return ExecResult(exit_code=code, output=text, timed_out=code in KILLED_EXIT_CODES)

    def read_file(self, path: str) -> str:
        rel = safe_relpath(path)
        result = self.exec(f"head -c {MAX_READ_BYTES} -- {shlex.quote(rel)}", 20)
        if result.exit_code != 0:
            raise SandboxError(result.output.strip() or f"cannot read {rel}")
        return result.output

    def write_file(self, path: str, content: str) -> None:
        rel = safe_relpath(path)
        data = content.encode("utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            parent = ""
            for part in posixpath.dirname(rel).split("/"):
                if part:
                    parent = posixpath.join(parent, part)
                    d = tarfile.TarInfo(parent)
                    d.type, d.mode = tarfile.DIRTYPE, 0o755
                    tar.addfile(d)
            info = tarfile.TarInfo(rel)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
        if not self._container.put_archive(WORKDIR, buf.getvalue()):
            raise SandboxError(f"cannot write {rel}")

    def close(self) -> None:
        try:
            self._container.remove(force=True)
        except Exception:  # best effort: the container is throwaway either way
            pass
