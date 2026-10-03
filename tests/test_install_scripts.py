"""The shell side (common.zsh, install.sh) must agree with the Python side.

The Whisper id and the "is this model fully cached?" rule live in two
languages: wristtales_voice_server.py and common.zsh (the scripts need them
before any venv exists). These tests fail when the two drift, and cover the
install.sh bootstrap and --help.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import wristtales_voice_server as wvs
from tests.test_capabilities import PARTIAL_SNAPSHOTS, WHISPER_DIR, make_snapshot

ROOT = Path(wvs.__file__).resolve().parent
ZSH = shutil.which("zsh")
pytestmark = pytest.mark.skipif(ZSH is None, reason="zsh not installed")

ENV_KEYS = ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME")


def zsh(code, env):
    return subprocess.run(
        [ZSH, "-c", f'source "{ROOT}/common.zsh"; {code}'],
        env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.fixture
def env(tmp_path):
    e = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    e["HOME"] = str(tmp_path / "home")
    e["HF_HOME"] = str(tmp_path / "hf")
    return e


def test_whisper_id_is_the_same_in_shell_and_python(env):
    assert zsh('print -rn -- "$WHISPER_MODEL"', env).stdout == wvs.WHISPER_MODEL_ID


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("kwargs", PARTIAL_SNAPSHOTS.values(), ids=PARTIAL_SNAPSHOTS.keys())
def test_shell_and_python_agree_on_snapshots(tmp_path, env, monkeypatch, kwargs, complete):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HF_HOME", env["HF_HOME"])
    make_snapshot(Path(env["HF_HOME"]) / "hub", **({} if complete else kwargs))
    py = wvs.hf_model_cached(wvs.WHISPER_MODEL_ID)
    sh = zsh("whisper_cached", env).returncode == 0
    assert py is sh is complete


@pytest.mark.parametrize(
    "setup",
    [
        {"HF_HUB_CACHE": "{t}/a"},
        {"HUGGINGFACE_HUB_CACHE": "{t}/b"},
        {"HF_HOME": "{t}/c"},
        {"XDG_CACHE_HOME": "{t}/d"},
        {},
        {"HF_HUB_CACHE": "{t}/a", "HF_HOME": "{t}/c", "XDG_CACHE_HOME": "{t}/d"},
    ],
)
def test_shell_and_python_resolve_the_same_cache_dir(tmp_path, env, monkeypatch, setup):
    for key in ENV_KEYS:
        env.pop(key, None)
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(wvs.Path, "home", classmethod(lambda cls: Path(env["HOME"])))
    for key, value in setup.items():
        env[key] = value.format(t=tmp_path)
        monkeypatch.setenv(key, env[key])
    assert zsh("hf_hub_cache", env).stdout.strip() == str(wvs.hf_hub_cache_dir())


# --- install.sh --------------------------------------------------------------


def run_installer(script, args, env, **extra):
    return subprocess.run(
        [ZSH, str(script), *args], env={**env, **extra},
        capture_output=True, text=True, timeout=60,
    )


def test_help_prints_the_whole_header(env):
    out = run_installer(ROOT / "install.sh", ["--help"], env).stdout
    assert "--check" in out and "SKIP_8BIT=1" in out and "WRISTTALES_REPO" in out
    assert "set -eu" not in out


def git(*args, cwd, env):
    genv = {**env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=genv, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def source_repo(tmp_path, env):
    """A throwaway 'upstream' with the real install.sh and common.zsh."""
    src = tmp_path / "upstream"
    src.mkdir()
    for name in ("install.sh", "common.zsh"):
        shutil.copy(ROOT / name, src / name)
    (src / "requirements.txt").write_text("")
    (src / "wristtales_voice_server.py").write_text("")
    git("init", "-q", "-b", "main", cwd=src, env=env)
    git("add", ".", cwd=src, env=env)
    git("commit", "-q", "-m", "init", cwd=src, env=env)
    return src


@pytest.fixture
def bootstrap_script(tmp_path):
    """install.sh alone in a directory, as under `curl | zsh`."""
    d = tmp_path / "bootstrap"
    d.mkdir()
    shutil.copy(ROOT / "install.sh", d / "install.sh")
    return d / "install.sh"


def test_bootstrap_clones_then_hands_over(tmp_path, env, source_repo, bootstrap_script):
    dest = tmp_path / "dest"
    r = run_installer(bootstrap_script, ["--help"], env,
                      WRISTTALES_DIR=str(dest), WRISTTALES_REPO=str(source_repo))
    assert r.returncode == 0, r.stderr
    assert (dest / ".git").is_dir() and "--check" in r.stdout


def test_bootstrap_pulls_an_existing_clone_before_handing_over(
    tmp_path, env, source_repo, bootstrap_script
):
    dest = tmp_path / "dest"
    git("clone", "-q", str(source_repo), str(dest), cwd=tmp_path, env=env)
    (source_repo / "NEWER").write_text("1")
    git("add", ".", cwd=source_repo, env=env)
    git("commit", "-q", "-m", "newer", cwd=source_repo, env=env)
    assert not (dest / "NEWER").exists()
    r = run_installer(bootstrap_script, ["--help"], env,
                      WRISTTALES_DIR=str(dest), WRISTTALES_REPO=str(source_repo))
    assert r.returncode == 0, r.stderr
    assert (dest / "NEWER").exists()


def test_bootstrap_refuses_a_foreign_clone(tmp_path, env, source_repo, bootstrap_script):
    other = tmp_path / "other"
    other.mkdir()
    git("init", "-q", cwd=other, env=env)
    git("remote", "add", "origin", "https://github.com/someone/else.git", cwd=other, env=env)
    r = run_installer(bootstrap_script, ["--help"], env,
                      WRISTTALES_DIR=str(other), WRISTTALES_REPO=str(source_repo))
    assert r.returncode == 1
    assert "ne tenhle" in r.stderr and "someone/else" in r.stderr


def test_bootstrap_refuses_a_clone_without_origin(tmp_path, env, source_repo, bootstrap_script):
    other = tmp_path / "other"
    other.mkdir()
    git("init", "-q", cwd=other, env=env)
    r = run_installer(bootstrap_script, ["--help"], env,
                      WRISTTALES_DIR=str(other), WRISTTALES_REPO=str(source_repo))
    assert r.returncode == 1 and "ne tenhle" in r.stderr


def test_bootstrap_accepts_the_official_origin_in_any_spelling(
    tmp_path, env, source_repo, bootstrap_script
):
    for url in (
        "https://github.com/PavelStancik/wristtales-voice-server.git",
        "https://github.com/PavelStancik/wristtales-voice-server",
        "git@github.com:PavelStancik/wristtales-voice-server.git",
    ):
        dest = tmp_path / ("d" + str(abs(hash(url))))
        shutil.copytree(source_repo, dest)
        git("remote", "add", "origin", url, cwd=dest, env=env)
        # Offline pull fails: tolerated with a warning, installer still runs.
        r = run_installer(bootstrap_script, ["--help"], env,
                          WRISTTALES_DIR=str(dest), HOME=env["HOME"],
                          GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="false",
                          https_proxy="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
                          WRISTTALES_REPO=str(source_repo))
        assert r.returncode == 0, (url, r.stderr)
        assert "stahuji novinky" in r.stdout and "--check" in r.stdout


def test_bootstrap_tolerates_a_dirty_clone(tmp_path, env, source_repo, bootstrap_script):
    dest = tmp_path / "dest"
    git("clone", "-q", str(source_repo), str(dest), cwd=tmp_path, env=env)
    (dest / "requirements.txt").write_text("local edit")
    (source_repo / "requirements.txt").write_text("upstream edit")
    git("add", ".", cwd=source_repo, env=env)
    git("commit", "-q", "-m", "conflicting", cwd=source_repo, env=env)
    r = run_installer(bootstrap_script, ["--help"], env,
                      WRISTTALES_DIR=str(dest), WRISTTALES_REPO=str(source_repo))
    assert r.returncode == 0 and "git pull neprošel" in r.stderr
