import os

from research_system.core.config import load_env_files


def _write(path, text):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".env").write_text(text, encoding="utf-8")


def test_root_env_wins_and_package_env_is_fallback(tmp_path, monkeypatch):
    for k in ("T_ONLY_ROOT", "T_ONLY_PKG", "T_BOTH"):
        monkeypatch.delenv(k, raising=False)
    root, pkg = tmp_path, tmp_path / "research_system"
    _write(root, "T_ONLY_ROOT=r\nT_BOTH=root\n")
    _write(pkg, "T_ONLY_PKG=p\nT_BOTH=pkg\n")
    load_env_files(root, pkg)
    assert os.environ["T_ONLY_ROOT"] == "r"
    assert os.environ["T_ONLY_PKG"] == "p"     # fallback supplies keys the root lacks
    assert os.environ["T_BOTH"] == "root"      # root wins on conflict


def test_missing_files_are_not_an_error(tmp_path):
    load_env_files(tmp_path, tmp_path / "research_system")
