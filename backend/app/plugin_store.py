from __future__ import annotations

import io
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from backend.app.skills import Skill, discover_skills

MAX_PLUGIN_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_FILES = 512


class PluginStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "plugins"
        self.skills_dir = self.root / "skills"
        self.tools_dir = self.root / "tools"

    def list_skills(self) -> list[Skill]:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        return discover_skills(self.skills_dir)

    def import_skill_zip(self, archive_bytes: bytes) -> list[Skill]:
        if len(archive_bytes) > MAX_PLUGIN_ARCHIVE_BYTES:
            raise OverflowError("ZIP 文件不能大于 10 MB")

        self.skills_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=self.skills_dir))
        installed_paths: list[Path] = []
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = archive.infolist()
                if not members or len(members) > MAX_ARCHIVE_FILES:
                    raise ValueError("ZIP 文件内容为空或文件数量超出限制")
                total_size = 0
                for member in members:
                    path = self._safe_member_path(member)
                    if member.is_dir():
                        (staging / path).mkdir(parents=True, exist_ok=True)
                        continue
                    total_size += member.file_size
                    if total_size > MAX_PLUGIN_ARCHIVE_BYTES:
                        raise OverflowError("ZIP 解压后内容不能大于 10 MB")
                    target = staging / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("xb") as destination:
                        copied = 0
                        while chunk := source.read(64 * 1024):
                            copied += len(chunk)
                            if copied > member.file_size or copied + total_size - member.file_size > MAX_PLUGIN_ARCHIVE_BYTES:
                                raise OverflowError("ZIP 解压后内容不能大于 10 MB")
                            destination.write(chunk)
                skills = discover_skills(staging)
                if not skills:
                    raise ValueError("ZIP 中没有有效的 Skill；每个 Skill 目录需包含有效的 SKILL.md")
                skill_names = [skill.name for skill in skills]
                if len(skill_names) != len(set(skill_names)):
                    raise ValueError("ZIP 中包含重复的 Skill 名称")
                existing = {skill.name for skill in self.list_skills()}
                duplicates = existing.intersection(skill_names)
                if duplicates:
                    raise FileExistsError(f"Skill 已加载：{', '.join(sorted(duplicates))}")

                installed_names: set[str] = set()
                for skill in skills:
                    source_dir = skill.skill_file.parent
                    target_dir = self.skills_dir / uuid.uuid4().hex
                    source_dir.replace(target_dir)
                    installed_paths.append(target_dir)
                    installed_names.add(skill.name)
            return [skill for skill in self.list_skills() if skill.name in installed_names]
        except zipfile.BadZipFile:
            raise ValueError("文件不是有效的 ZIP") from None
        except Exception:
            for path in installed_paths:
                shutil.rmtree(path, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def delete_skill(self, name: str) -> bool:
        for skill in self.list_skills():
            if skill.name == name:
                shutil.rmtree(skill.skill_file.parent, ignore_errors=False)
                return True
        return False

    @staticmethod
    def _safe_member_path(member: zipfile.ZipInfo) -> Path:
        raw_name = member.filename
        path = PurePosixPath(raw_name)
        mode = member.external_attr >> 16
        if (
            not raw_name
            or "\\" in raw_name
            or ":" in raw_name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or stat.S_ISLNK(mode)
        ):
            raise ValueError("ZIP 包含不安全的文件路径")
        return Path(*path.parts)

