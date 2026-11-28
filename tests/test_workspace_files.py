import base64
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.workspace_files import (
    list_workspace_entries,
    read_workspace_text,
    resolve_workspace_file,
    write_workspace_text,
)


def status(error: pytest.ExceptionInfo[HTTPException]) -> int:
    return error.value.status_code


def test_rejects_parent_traversal(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    with pytest.raises(HTTPException) as error:
        resolve_workspace_file(root, '../secret.txt')
    assert status(error) == 422


@pytest.mark.parametrize('relative_path', ['C:/secret.txt', '/secret.txt', '\\server\\share\\secret.txt', 'a/../secret.txt', 'a/./secret.txt', 'a\x00b'])
def test_rejects_absolute_or_ambiguous_paths(tmp_path, relative_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    with pytest.raises(HTTPException) as error:
        resolve_workspace_file(root, relative_path)
    assert status(error) == 422


def test_lists_and_reads_only_relative_workspace_files(tmp_path):
    root = tmp_path / 'workspace'
    (root / 'docs').mkdir(parents=True)
    (root / 'note.txt').write_text('root', encoding='utf-8')
    (root / 'docs' / 'note.md').write_text('hello', encoding='utf-8')
    entries = list_workspace_entries(root, '')
    assert [(item['name'], item['kind']) for item in entries] == [('docs', 'directory'), ('note.txt', 'file')]
    content, revision = read_workspace_text(root, 'docs/note.md')
    assert content == 'hello'
    assert len(revision) == 64


def test_rejects_missing_paths_and_directory_as_file(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'folder').mkdir()
    with pytest.raises(HTTPException) as missing:
        resolve_workspace_file(root, 'missing.txt')
    assert status(missing) == 404
    with pytest.raises(HTTPException) as directory:
        read_workspace_text(root, 'folder')
    assert status(directory) == 422


def test_write_requires_current_revision_and_replaces_text(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    target = root / 'note.txt'
    target.write_text('old', encoding='utf-8')
    _, revision = read_workspace_text(root, 'note.txt')
    new_revision = write_workspace_text(root, 'note.txt', 'new', revision)
    assert target.read_text(encoding='utf-8') == 'new'
    assert new_revision != revision
    with pytest.raises(HTTPException) as error:
        write_workspace_text(root, 'note.txt', 'stale', revision)
    assert status(error) == 409
    assert target.read_text(encoding='utf-8') == 'new'


@pytest.mark.parametrize('suffix', ['.bin', '.png', '.pdf'])
def test_only_editable_text_suffixes_can_be_read_or_written(tmp_path, suffix):
    root = tmp_path / 'workspace'
    root.mkdir()
    target = root / f'payload{suffix}'
    target.write_bytes(b'payload')
    with pytest.raises(HTTPException) as error:
        read_workspace_text(root, target.name)
    assert status(error) == 415


def test_rejects_invalid_utf8_and_oversized_text(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    invalid = root / 'invalid.txt'
    invalid.write_bytes(b'\xff')
    with pytest.raises(HTTPException) as invalid_error:
        read_workspace_text(root, invalid.name)
    assert status(invalid_error) == 422

    oversized = root / 'large.txt'
    oversized.write_bytes(b'a' * (2 * 1024 * 1024 + 1))
    with pytest.raises(HTTPException) as size_error:
        read_workspace_text(root, oversized.name)
    assert status(size_error) == 413


def test_directory_listing_has_limit_and_directory_first(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'z-directory').mkdir()
    (root / 'A-file.txt').write_text('x', encoding='utf-8')
    for index in range(1001):
        (root / f'file-{index:04d}.txt').write_text('x', encoding='utf-8')
    entries = list_workspace_entries(root, '')
    assert len(entries) == 1000
    assert entries[0]['kind'] == 'directory'
    assert entries[1]['name'].lower() == 'a-file.txt'


def test_rejects_symlink_escape(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('secret', encoding='utf-8')
    link = root / 'link.txt'
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks are unavailable on this platform')
    with pytest.raises(HTTPException) as error:
        resolve_workspace_file(root, link.name)
    assert status(error) == 422


PNG_1X1 = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jN8kAAAAASUVORK5CYII=')


def _file_app(tmp_path):
    return create_app(tmp_path / 'harness.sqlite3', data_dir=tmp_path / 'data')


def test_session_file_api_lists_reads_and_saves_with_revision(tmp_path):
    with TestClient(_file_app(tmp_path)) as client:
        session = client.post('/api/sessions', json={}).json()
        root = Path(session['workspace']['path'])
        (root / 'docs').mkdir()
        (root / 'docs' / 'note.md').write_text('before', encoding='utf-8')
        (root / 'photo.png').write_bytes(PNG_1X1)

        listing = client.get(f"/api/sessions/{session['id']}/workspace-files", params={'path': ''})
        assert listing.status_code == 200
        assert [item['path'] for item in listing.json()['entries']] == ['docs', 'photo.png']
        assert str(root) not in listing.text

        content_url = f"/api/sessions/{session['id']}/workspace-files/content"
        loaded = client.get(content_url, params={'path': 'docs/note.md'})
        assert loaded.status_code == 200
        assert loaded.json()['content'] == 'before'
        revision = loaded.json()['revision']
        saved = client.put(content_url, json={
            'path': 'docs/note.md', 'content': 'after', 'expected_revision': revision,
        })
        assert saved.status_code == 200
        assert saved.json()['content'] == 'after'
        assert (root / 'docs' / 'note.md').read_text(encoding='utf-8') == 'after'
        stale = client.put(content_url, json={
            'path': 'docs/note.md', 'content': 'stale', 'expected_revision': revision,
        })
        assert stale.status_code == 409

        preview = client.get(
            f"/api/sessions/{session['id']}/workspace-files/preview",
            params={'path': 'photo.png'},
        )
        assert preview.status_code == 200
        assert preview.content == PNG_1X1
        assert preview.headers['content-type'].startswith('image/png')
        assert preview.headers['content-disposition'].startswith('inline')
        assert preview.headers['x-content-type-options'] == 'nosniff'


def test_session_file_api_downloads_unknown_binary_but_does_not_preview_active_content(tmp_path):
    with TestClient(_file_app(tmp_path)) as client:
        session = client.post('/api/sessions', json={}).json()
        root = Path(session['workspace']['path'])
        (root / 'archive.bin').write_bytes(b'\x00\x01binary')
        (root / 'page.html').write_text('<script>alert(1)</script>', encoding='utf-8')
        base = f"/api/sessions/{session['id']}/workspace-files"

        download = client.get(f'{base}/download', params={'path': 'archive.bin'})
        assert download.status_code == 200
        assert download.content == b'\x00\x01binary'
        assert 'attachment' in download.headers['content-disposition']
        assert download.headers['x-content-type-options'] == 'nosniff'

        for name in ('archive.bin', 'page.html'):
            response = client.get(f'{base}/preview', params={'path': name})
            assert response.status_code == 415


def test_session_file_api_rejects_unknown_session_sandbox_and_path_escape(tmp_path):
    app = _file_app(tmp_path)
    app.state.sandbox_executor.is_configured = lambda: True
    with TestClient(app) as client:
        missing = client.get('/api/sessions/not-a-session/workspace-files', params={'path': ''})
        assert missing.status_code == 404

        session = client.post('/api/sessions', json={}).json()
        sandbox = client.put(
            f"/api/sessions/{session['id']}/workspace",
            json={'group_id': None, 'execution_mode': 'sandbox'},
        )
        assert sandbox.status_code == 200
        base = f"/api/sessions/{session['id']}/workspace-files"
        assert client.get(base, params={'path': ''}).status_code == 409
        assert client.get(f'{base}/content', params={'path': '../secret.txt'}).status_code == 409


def test_session_file_api_rejects_escape_and_symlink(tmp_path):
    with TestClient(_file_app(tmp_path)) as client:
        session = client.post('/api/sessions', json={}).json()
        root = Path(session['workspace']['path'])
        (root / 'note.txt').write_text('safe', encoding='utf-8')
        outside = tmp_path / 'outside.txt'
        outside.write_text('secret', encoding='utf-8')
        link = root / 'outside.txt'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            link = None
        base = f"/api/sessions/{session['id']}/workspace-files"
        assert client.get(f'{base}/content', params={'path': '../outside.txt'}).status_code == 422
        if link is not None:
            assert client.get(f'{base}/content', params={'path': link.name}).status_code == 422


def test_rejects_intermediate_symlink_directory_escape(tmp_path):
    import os
    import subprocess

    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside'
    (outside / 'nested').mkdir(parents=True)
    (outside / 'nested' / 'secret.txt').write_text('secret', encoding='utf-8')
    link = root / 'linked'
    try:
        if os.name == 'nt':
            result = subprocess.run(
                ['cmd.exe', '/c', 'mklink', '/J', str(link), str(outside)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                pytest.skip('Windows junction creation is unavailable')
        else:
            link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('directory links are unavailable on this platform')
    with pytest.raises(HTTPException) as error:
        list_workspace_entries(root, 'linked/nested')
    assert status(error) == 422
