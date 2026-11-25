import csv
import io
import json
import re
import zipfile
from pathlib import PurePath

from docx import Document
from pypdf import PdfReader


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_CHARS = 2_000_000
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200
MAX_DOCX_MEMBERS = 10_000
SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".pdf", ".docx"}


class DocumentParseError(ValueError):
    """A supported document could not be safely converted to searchable text."""


def safe_storage_extension(filename: str) -> str:
    extension = PurePath(filename.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise DocumentParseError(f"不支持此文件格式；支持格式：{supported}")
    return extension


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentParseError("文件不是有效的 UTF-8 编码") from error


def _parse_csv(content: bytes) -> str:
    text = _decode_utf8(content)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise DocumentParseError("CSV 格式无效，请检查引号和分隔符") from error
    if not rows:
        return ""
    headers = rows[0]
    output = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) > 10_000 or any(len(field) > 100_000 for field in row):
            raise DocumentParseError("CSV 字段过大，无法导入")
        if headers and len(row) != len(headers):
            raise DocumentParseError(f"CSV 第 {row_number} 行字段数量与表头不匹配")
        output.append(" | ".join(
            f"{headers[index] if index < len(headers) else f'列{index + 1}'}: {value}"
            for index, value in enumerate(row)
        ))
    return "\n".join(output)


def _validate_docx_archive(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise DocumentParseError("DOCX 压缩包包含过多文件，已拒绝解析")
            uncompressed = 0
            for member in members:
                normalized = member.filename.replace("\\", "/")
                if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
                    raise DocumentParseError("DOCX 压缩包包含不安全路径")
                if any(part == ".." for part in normalized.split("/")):
                    raise DocumentParseError("DOCX 压缩包包含不安全路径")
                uncompressed += member.file_size
                if uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise DocumentParseError("DOCX 解压内容过大，已拒绝解析")
                if member.file_size and member.compress_size == 0:
                    raise DocumentParseError("DOCX 压缩比例异常，已拒绝解析")
                if member.compress_size and member.file_size / member.compress_size > MAX_DOCX_COMPRESSION_RATIO:
                    raise DocumentParseError("DOCX 压缩比例异常，已拒绝解析")
    except zipfile.BadZipFile as error:
        raise DocumentParseError("DOCX 文件损坏或格式无效") from error


def _parse_docx(content: bytes) -> str:
    _validate_docx_archive(content)
    try:
        document = Document(io.BytesIO(content))
    except Exception as error:
        raise DocumentParseError("DOCX 文件损坏或格式无效") from error
    chunks = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
    for table in document.tables:
        for row in table.rows:
            chunks.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(chunks)


def _parse_pdf(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:
        raise DocumentParseError("PDF 文件损坏或无法提取文本") from error
    text = "\n".join(page for page in pages if page.strip())
    if not text.strip():
        raise DocumentParseError("PDF 没有可提取文本；不支持扫描件/OCR")
    return text


def parse_document(filename: str, content: bytes) -> str:
    extension = safe_storage_extension(filename)
    if not isinstance(content, bytes):
        raise DocumentParseError("文件内容无效")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentParseError("单个文件不能大于 20 MB")

    if extension in {".md", ".markdown", ".txt"}:
        text = _decode_utf8(content)
    elif extension == ".json":
        try:
            value = json.loads(_decode_utf8(content))
        except (json.JSONDecodeError, DocumentParseError) as error:
            raise DocumentParseError("JSON 格式无效") from error
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    elif extension == ".csv":
        text = _parse_csv(content)
    elif extension == ".pdf":
        text = _parse_pdf(content)
    else:
        text = _parse_docx(content)

    if len(text) > MAX_EXTRACTED_CHARS:
        raise DocumentParseError("提取文本超过 2,000,000 字符限制")
    return text
