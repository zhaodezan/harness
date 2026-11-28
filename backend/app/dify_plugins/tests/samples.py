import io
import zipfile


FILES = {
    'manifest.yaml': '''version: 1.2.3
type: plugin
author: sample
name: echo
label: {en_US: Echo}
plugins: {tools: [provider/echo.yaml]}
resource: {permission: {tool: {enabled: true}}}
meta: {version: '0.0.1', runner: {language: python, version: '3.12', entrypoint: main}}
''',
    'main.py': 'from dify_plugin import Plugin, DifyPluginEnv\nPlugin(DifyPluginEnv()).run()\n',
    'provider/echo.yaml': '''identity: {name: echo}
extra: {python: {source: provider/echo.py}}
credentials_for_provider:
  sample_key: {type: secret-input, required: false, label: {en_US: Example key}}
tools: [tools/echo.yaml]
''',
    'provider/echo.py': 'from dify_plugin import ToolProvider\nclass EchoProvider(ToolProvider):\n    def _validate_credentials(self, credentials):\n        pass\n',
    'tools/echo.yaml': '''identity: {name: echo, label: {en_US: Echo}}
description: {human: {en_US: Repeat text}, llm: Repeat text}
extra: {python: {source: tools/echo.py}}
parameters:
- {name: text, type: string, form: llm, required: true}
- {name: count, type: number, form: form, default: 1}
''',
    'tools/echo.py': 'from dify_plugin import Tool\nclass Echo(Tool):\n    def _invoke(self, tool_parameters):\n        yield self.create_text_message(tool_parameters["text"] * int(tool_parameters.get("count", 1)))\n',
    'requirements.txt': 'jsonpath-ng==1.12.0\n',
}


def package(changes=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in (FILES | (changes or {})).items():
            if content is not None:
                entry = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, content)
    return buffer.getvalue()
