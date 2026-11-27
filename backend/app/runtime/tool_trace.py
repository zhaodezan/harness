import json


_PREVIEW_LIMIT = 4000
_SENSITIVE_FIELDS = ('password', 'passwd', 'passphrase', 'secret', 'token', 'apikey', 'privatekey', 'authorization', 'credential', 'cookie')
_REDACTED = '[REDACTED]'
_JS_LINE_SEPARATORS = {cp: chr(cp).encode('unicode_escape').decode('ascii') for cp in (0x2028, 0x2029)}


def is_sensitive_field(key) -> bool:
    normalized = str(key).lower().replace('-', '').replace('_', '')
    return any(marker in normalized for marker in _SENSITIVE_FIELDS)


def redact_sensitive_fields(value):
    if isinstance(value, dict):
        redacted = {
            key: _REDACTED if is_sensitive_field(key) else redact_sensitive_fields(item)
            for key, item in value.items()
        }
        columns, rows = redacted.get('columns'), redacted.get('rows')
        if isinstance(columns, list) and isinstance(rows, list):
            # Database query results identify cells by column position rather than by object key.
            sensitive_columns = {i for i, name in enumerate(columns) if isinstance(name, str) and is_sensitive_field(name)}
            if sensitive_columns:
                redacted['rows'] = [
                    [_REDACTED if i in sensitive_columns else cell for i, cell in enumerate(row)]
                    if isinstance(row, list) else row
                    for row in rows
                ]
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_fields(item) for item in value]
    return value


def _preview(value) -> dict:
    try:
        text = value if isinstance(value, str) else json.dumps(
            redact_sensitive_fields(value), ensure_ascii=False, indent=2, default=str,
        )
    except (TypeError, ValueError, RecursionError):
        text = '无法生成预览'
    return {
        'text': text[:_PREVIEW_LIMIT].encode('utf-8', errors='replace').decode('utf-8'),
        'truncated': len(text) > _PREVIEW_LIMIT,
    }


def encode_trace_payload(payload: dict) -> str:
    # Keep non-ASCII text readable while escaping JavaScript's other line terminators.
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).translate(_JS_LINE_SEPARATORS)


def tool_trace_details(tool_name: str, arguments: dict, outcome: dict) -> dict:
    """Build previews for the live conversation stream, not for persisted run events."""
    details = {'tool_name': tool_name, 'input': _preview(arguments)}
    if 'error' in outcome:
        details['error'] = _preview(outcome['error'])
    else:
        details['output'] = _preview(outcome['result'])
    return details
