import pytest

from agentbuddy.providers.native import ProviderFailure
from agentbuddy.runtime.delivery import Delivery
from agentbuddy.runtime.files import manifest


def test_missing_unchanged_and_deleted_files_cannot_pass(tmp_path):
    (tmp_path / "old.txt").write_text("old")
    baseline = {f["path"]: f["sha256"] for f in manifest(tmp_path)}
    contract = Delivery(
        kind="files", required=["new.txt"], changed=["new.txt"], deleted=["old.txt"]
    )
    with pytest.raises(ProviderFailure, match="缺失文件"):
        contract.verify(tmp_path, baseline)
    (tmp_path / "new.txt").write_text("new")
    with pytest.raises(ProviderFailure, match="未删除"):
        contract.verify(tmp_path, baseline)
    (tmp_path / "old.txt").unlink()
    contract.verify(tmp_path, baseline)
    existing = Delivery(kind="files", required=["new.txt"], changed=["new.txt"], deleted=[])
    with pytest.raises(ProviderFailure, match="未修改"):
        existing.verify(tmp_path, {f["path"]: f["sha256"] for f in manifest(tmp_path)})
    Delivery(kind="answer", required=[], changed=[], deleted=[]).verify(tmp_path, baseline)


@pytest.mark.parametrize(
    "data",
    [
        dict(kind="files", required=[], changed=[], deleted=[]),
        dict(kind="files", required=["../escape"], changed=[], deleted=[]),
        dict(kind="files", required=["a"], changed=[], deleted=["a"]),
        dict(kind="answer", required=["a"], changed=[], deleted=[]),
    ],
)
def test_invalid_contract_rejected(data):
    with pytest.raises(ValueError):
        Delivery(**data)


def test_context_estimate_is_not_utf8_byte_count():
    from agentbuddy.providers.context import estimate_tokens

    assert estimate_tokens("abcdef") == 2
    assert estimate_tokens("中文") == 4
    assert estimate_tokens("{}[]") == 4


def test_content_requirements_are_checked_in_each_file(tmp_path):
    from agentbuddy.runtime.delivery import ContentCheck

    (tmp_path / "a.txt").write_text("2026-11-11", encoding="utf-8")
    (tmp_path / "b.txt").write_text("wrong date", encoding="utf-8")
    contract = Delivery(
        kind="files",
        required=["a.txt", "b.txt"],
        changed=[],
        deleted=[],
        content_checks=[
            ContentCheck(path=p, expected="2026-11-11", source="用户指定日期")
            for p in ["a.txt", "b.txt"]
        ],
    )
    with pytest.raises(ProviderFailure, match="b.txt"):
        contract.verify(tmp_path, {})
    (tmp_path / "b.txt").write_text("2026-11-11", encoding="utf-8")
    contract.verify(tmp_path, {})
