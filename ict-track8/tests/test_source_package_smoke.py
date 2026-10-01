import importlib.util
from pathlib import Path
import stat
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('package_smoke', Path(__file__).resolve().parents[2] / 'tools/smoke_source_package.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.mark.parametrize('name', ['../escape.txt', '/escape.txt', 'C:/secret',
                                 'runtime/config.json', 'x/model_config.json', '.env'])
def test_unsafe_zip_never_extracts_any_member(tmp_path, name):
    package = tmp_path / 'bad.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('safe.txt', 'safe')
        info = zipfile.ZipInfo('placeholder')
        info.filename = name
        archive.writestr(info, 'bad')
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(ValueError):
        tool.safe_extract(package, target)
    assert list(target.iterdir()) == []


def test_symlink_zip_rejected(tmp_path):
    package = tmp_path / 'link.zip'
    info = zipfile.ZipInfo('link')
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr(info, '../target')
    with pytest.raises(ValueError):
        tool.safe_extract(package, tmp_path / 'target')


def test_child_env_excludes_credentials_provider_proxies_and_pythonpath():
    observed = tool.isolated_environment({'PATH': 'runtime', 'SYSTEMROOT': 'system', 'USERNAME': 'local-user',
        'ICT8_OPENAI_API_KEY': 'secret', 'OPENAI_API_KEY': 'secret', 'HTTPS_PROXY': 'secret',
        'PYTHONPATH': 'parent-project', 'ICT8_KNOWLEDGE_ROOT': 'parent-data', 'ICT8_MODEL_ENABLED': 'true'})
    assert observed['PATH'] == 'runtime'
    assert observed['USERNAME'] == 'local-user'
    assert observed['ICT8_MODEL_ENABLED'] == 'false'
    assert not any(key in observed for key in ('OPENAI_API_KEY', 'ICT8_OPENAI_API_KEY', 'HTTPS_PROXY', 'PYTHONPATH', 'ICT8_KNOWLEDGE_ROOT'))
    assert observed['HF_HUB_OFFLINE'] == '1'


def test_valid_zip_extracts_actual_bytes(tmp_path):
    package = tmp_path / 'safe.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('nested/file.txt', b'actual')
    tool.safe_extract(package, tmp_path / 'target')
    assert (tmp_path / 'target/nested/file.txt').read_bytes() == b'actual'
