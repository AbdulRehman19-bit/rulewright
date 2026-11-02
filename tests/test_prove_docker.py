"""Real-container tests of the sandbox guarantees. Skipped when Docker is not reachable."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from rulewright.prove.sandbox import DEFAULT_IMAGE, DockerSandbox, SandboxError


def _docker_ready() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="Docker is not available")


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[DockerSandbox]:
    (tmp_path / "hello.txt").write_bytes(b"from the old tree\n")  # bytes: no CRLF on Windows
    box = DockerSandbox(tmp_path)
    yield box
    box.close()


def test_runs_commands_in_the_mounted_copy(sandbox: DockerSandbox) -> None:
    result = sandbox.exec("pwd && cat hello.txt", 20)
    assert result.exit_code == 0
    assert result.output.split() == ["/work", "from", "the", "old", "tree"]


def test_files_round_trip_through_the_container_and_reach_the_host_copy(
    sandbox: DockerSandbox, tmp_path: Path
) -> None:
    sandbox.write_file("pkg/sub/new.py", "x = 'é'\n")
    assert sandbox.read_file("pkg/sub/new.py") == "x = 'é'\n"
    assert (tmp_path / "pkg" / "sub" / "new.py").read_text(encoding="utf-8") == "x = 'é'\n"
    assert sandbox.read_file("hello.txt") == "from the old tree\n"
    with pytest.raises(SandboxError):
        sandbox.read_file("missing.txt")
    with pytest.raises(SandboxError):
        sandbox.write_file("../escape.txt", "x")


def test_there_is_no_network(sandbox: DockerSandbox) -> None:
    probe = (
        "import socket,sys\n"
        "try:\n socket.create_connection(('1.1.1.1', 53), timeout=3)\n print('CONNECTED')\n"
        "except OSError:\n print('BLOCKED')\n"
    )
    sandbox.write_file("probe.py", probe)
    assert sandbox.exec("python probe.py", 20).output.strip() == "BLOCKED"


def test_host_environment_and_secrets_are_not_visible(
    sandbox: DockerSandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-super-secret-value")
    env = sandbox.exec("env", 20).output
    assert "secret" not in env and "API_KEY" not in env


def test_capabilities_are_dropped(sandbox: DockerSandbox) -> None:
    status = sandbox.exec("grep -E '^Cap(Eff|Prm)' /proc/self/status", 20).output
    assert status.count("0000000000000000") == 2


def test_a_runaway_command_is_killed(sandbox: DockerSandbox) -> None:
    result = sandbox.exec("sleep 60", 2)
    assert result.timed_out


def test_the_memory_limit_applies(tmp_path: Path) -> None:
    box = DockerSandbox(tmp_path, memory="64m")
    try:
        result = box.exec("python -c \"x = bytearray(400 * 1024 * 1024); print('ALLOCATED')\"", 30)
        assert "ALLOCATED" not in result.output and result.exit_code != 0
    finally:
        box.close()


def test_close_removes_the_container(tmp_path: Path) -> None:
    import docker

    box = DockerSandbox(tmp_path)
    cid = box._container.id
    box.close()
    with pytest.raises(docker.errors.NotFound):
        docker.from_env().containers.get(cid)


def test_files_written_in_belong_to_the_container_user(sandbox: DockerSandbox) -> None:
    sandbox.write_file("owned/dir/f.txt", "x")
    if not hasattr(os, "getuid"):
        pytest.skip("ownership is not mapped on this host")
    uid = str(os.getuid())
    owners = sandbox.exec("stat -c %u /work/owned /work/owned/dir /work/owned/dir/f.txt", 20)
    assert owners.output.split() == [uid, uid, uid]


def test_the_default_image_is_the_documented_one() -> None:
    assert DEFAULT_IMAGE == "python:3.12-slim" and os.name  # guards an accidental change
