import pytest

from backend.app.dify_plugins.package import PackageError, inspect_package
from backend.app.dify_plugins.tests.samples import FILES, package


def test_inspects_standalone_tool_without_importing_plugin():
    value = inspect_package(package({'main.py': 'raise RuntimeError("never run on host")'}), 'sample/echo', '1.2.3')
    assert value.plugin_id == 'sample/echo'
    assert value.label == 'Echo'
    assert value.tools[0]['schema']['required'] == ['text']
    assert value.tools[0]['defaults'] == {'count': 1}
    assert value.credentials['sample_key']['type'] == 'secret-input'
    assert len(value.digest) == 64


@pytest.mark.parametrize('name', ['../escape', '/root', 'C:/drive', 'tools\\x.py', 'foo/../x', 'foo//x', 'tools/CON.py', 'Tools/Echo.py'])
def test_rejects_unsafe_or_ambiguous_member_names(name):
    # Windows' ZipFile writer normalizes backslashes, so construct that member on the wire.
    if '\\' in name:
        placeholder = name.replace('\\', '-')
        data = package({placeholder: b'not extracted'}).replace(placeholder.encode(), name.encode())
    else:
        data = package({name: b'not extracted'})
    with pytest.raises(PackageError):
        inspect_package(data)


def test_rejects_plugin_identity_mismatch():
    with pytest.raises(PackageError, match='身份或版本'):
        inspect_package(package(), 'sample/echo', '2.0.0')


@pytest.mark.parametrize('contents', ['-r nested.txt', 'thing @ https://example.org/thing.whl', '--extra-index-url https://x.test'])
def test_rejects_uncontrolled_dependency_sources(contents):
    with pytest.raises(PackageError, match='依赖'):
        inspect_package(package({'requirements.txt': contents}))


def test_rejects_dynamic_tool_and_non_tool_package():
    tool = FILES['tools/echo.yaml'].replace('type: string', 'type: dynamic-select')
    with pytest.raises(PackageError, match='参数类型'):
        inspect_package(package({'tools/echo.yaml': tool}))
    manifest = FILES['manifest.yaml'].replace('tools: [provider/echo.yaml]', 'models: [provider/echo.yaml]')
    with pytest.raises(PackageError, match='Python 工具'):
        inspect_package(package({'manifest.yaml': manifest}))


def test_accepts_standard_dify_manifest_without_optional_type_field():
    data = package({'manifest.yaml': FILES['manifest.yaml'].replace('type: plugin\n', '')})
    assert inspect_package(data).plugin_id == 'sample/echo'


def test_preserves_numeric_limits_from_dify_tool_parameters():
    source = FILES['tools/echo.yaml'].replace(
        '{name: count, type: number, form: form, default: 1}',
        '{name: count, type: number, form: form, default: 1, min: 1, max: 10}',
    )
    value = inspect_package(package({'tools/echo.yaml': source}))
    assert value.tools[0]['schema']['properties']['count']['minimum'] == 1
    assert value.tools[0]['schema']['properties']['count']['maximum'] == 10


def test_rejects_fixed_dependency_replacing_harness_sdk():
    with pytest.raises(PackageError, match='运行器支持'):
        inspect_package(package({'requirements.txt': 'dify-plugin==0.5.0\n'}))
    with pytest.raises(PackageError, match='版本'):
        inspect_package(package({'requirements.txt': 'flask>=3.0\n'}))
