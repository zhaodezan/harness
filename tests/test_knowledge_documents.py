import io
import zipfile

import pytest

from backend.app.knowledge_documents import (
    DocumentParseError,
    MAX_EXTRACTED_CHARS,
    MAX_UPLOAD_BYTES,
    parse_document,
    safe_storage_extension,
)


def test_parse_text_json_and_csv_documents():
    assert parse_document("notes.md", "# Title\nbody".encode()) == "# Title\nbody"
    assert parse_document("notes.markdown", b"text") == "text"
    assert parse_document("notes.txt", "hello".encode()) == "hello"
    assert parse_document("data.json", b'{"b":2,"a":1}') == '{\n  "a": 1,\n  "b": 2\n}'
    assert "name: Ada" in parse_document("people.csv", b"name,role\nAda,Engineer\n")


def test_rejects_unsupported_extension_and_invalid_encodings():
    with pytest.raises(DocumentParseError, match="支持"):
        parse_document("script.py", b"print('unsafe')")
    with pytest.raises(DocumentParseError, match="UTF-8"):
        parse_document("bad.txt", b"\xff")
    with pytest.raises(DocumentParseError, match="JSON"):
        parse_document("bad.json", b"{")
    with pytest.raises(DocumentParseError, match="CSV"):
        parse_document("bad.csv", b'"unterminated')


def test_rejects_upload_and_extracted_text_over_limits():
    with pytest.raises(DocumentParseError, match="20 MB"):
        parse_document("large.txt", b"x" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(DocumentParseError, match="2,000,000"):
        parse_document("too-long.txt", ("x" * (MAX_EXTRACTED_CHARS + 1)).encode())


def test_safe_extension_never_uses_untrusted_path_components():
    assert safe_storage_extension("../../outside.MD") == ".md"
    with pytest.raises(DocumentParseError):
        safe_storage_extension("no-extension")
    with pytest.raises(DocumentParseError):
        safe_storage_extension("file.exe")


def test_scan_pdf_is_rejected_with_ocr_message():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(DocumentParseError, match="不支持扫描件/OCR"):
        parse_document("scan.pdf", output.getvalue())


def test_extracts_pdf_text_and_docx_paragraphs_and_tables(monkeypatch):
    from docx import Document
    from pypdf import PdfWriter
    import backend.app.knowledge_documents as parsers

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    output = io.BytesIO()
    writer.write(output)

    class FakePage:
        def extract_text(self):
            return "BM25 searchable PDF text"

    class FakePdfReader:
        def __init__(self, _stream, strict=False):
            self.pages = [FakePage()]

    monkeypatch.setattr(parsers, "PdfReader", FakePdfReader)
    pdf_text = parse_document("guide.pdf", output.getvalue())
    assert "BM25 searchable PDF text" in pdf_text

    document = Document()
    document.add_paragraph("DOCX paragraph")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Header"
    table.cell(0, 1).text = "Value"
    docx_output = io.BytesIO()
    document.save(docx_output)
    docx_text = parse_document("guide.docx", docx_output.getvalue())
    assert "DOCX paragraph" in docx_text
    assert "Header" in docx_text and "Value" in docx_text


def test_rejects_corrupt_docx_archive():
    with pytest.raises(DocumentParseError, match="DOCX"):
        parse_document("broken.docx", b"not a zip")


def test_rejects_docx_with_unsafe_zip_member_path():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../evil.xml", "payload")
        zipped.writestr("[Content_Types].xml", "not valid docx")
    with pytest.raises(DocumentParseError, match="DOCX"):
        parse_document("unsafe.docx", archive.getvalue())
