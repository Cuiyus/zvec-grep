use std::borrow::Cow;

use crate::domain::FileFormat;

/// Decodes a known text format for indexing. Format sniffing remains strict.
pub(crate) fn decode_index_text<'a>(
    formats: &[FileFormat],
    bytes: &'a [u8],
) -> Option<Cow<'a, str>> {
    if has_bom(bytes) {
        return decode_text(bytes, true);
    }
    if formats.contains(&FileFormat::Python) && python_declares_latin_one(bytes) {
        return Some(Cow::Owned(
            bytes.iter().map(|byte| char::from(*byte)).collect(),
        ));
    }
    if let Some(text) = decode_text(bytes, true) {
        return Some(text);
    }
    (!looks_binary(bytes)).then(|| String::from_utf8_lossy(bytes))
}

fn has_bom(bytes: &[u8]) -> bool {
    bytes.starts_with(b"\xef\xbb\xbf")
        || bytes.starts_with(b"\xff\xfe")
        || bytes.starts_with(b"\xfe\xff")
        || bytes.starts_with(b"\x00\x00\xfe\xff")
}

fn looks_binary(bytes: &[u8]) -> bool {
    bytes
        .iter()
        .any(|byte| matches!(*byte, 0 | 1..=8 | 11 | 14..=31))
}

fn python_declares_latin_one(bytes: &[u8]) -> bool {
    for line in bytes.split(|byte| *byte == b'\n').take(2) {
        let trimmed = line.trim_ascii_start();
        let Some(comment) = trimmed.strip_prefix(b"#") else {
            return false;
        };
        for (start, _) in comment.windows(6).enumerate() {
            if &comment[start..start + 6] != b"coding" {
                continue;
            }
            let rest = comment[start + 6..]
                .strip_prefix(b":")
                .or_else(|| comment[start + 6..].strip_prefix(b"="));
            let Some(rest) = rest else { continue };
            let label = rest.trim_ascii_start();
            let end = label
                .iter()
                .position(|byte| {
                    !byte.is_ascii_alphanumeric() && !matches!(*byte, b'-' | b'_' | b'.')
                })
                .unwrap_or(label.len());
            let label = &label[..end];
            if [
                b"latin1".as_slice(),
                b"latin-1".as_slice(),
                b"latin_1".as_slice(),
                b"iso-8859-1".as_slice(),
                b"iso_8859_1".as_slice(),
                b"iso8859-1".as_slice(),
                b"iso8859_1".as_slice(),
            ]
            .iter()
            .any(|alias| label.eq_ignore_ascii_case(alias))
            {
                return true;
            }
        }
    }
    false
}

/// Decodes UTF-8, or UTF-16/32 with a BOM, without replacing invalid input.
pub(crate) fn decode_text(bytes: &[u8], complete: bool) -> Option<Cow<'_, str>> {
    // UTF-32 LE must precede UTF-16 LE because their BOMs share the first two bytes.
    if let Some(body) = bytes.strip_prefix(b"\xff\xfe\x00\x00") {
        return decode_utf32(body, true, complete).map(Cow::Owned);
    }
    if let Some(body) = bytes.strip_prefix(b"\x00\x00\xfe\xff") {
        return decode_utf32(body, false, complete).map(Cow::Owned);
    }
    if let Some(body) = bytes.strip_prefix(b"\xff\xfe") {
        return decode_utf16(body, true, complete).map(Cow::Owned);
    }
    if let Some(body) = bytes.strip_prefix(b"\xfe\xff") {
        return decode_utf16(body, false, complete).map(Cow::Owned);
    }
    let body = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
    let text = match std::str::from_utf8(body) {
        Ok(text) => text,
        Err(error) if !complete && error.error_len().is_none() => {
            std::str::from_utf8(&body[..error.valid_up_to()]).ok()?
        }
        Err(_) => return None,
    };
    Some(Cow::Borrowed(text))
}

fn decode_utf16(bytes: &[u8], little_endian: bool, complete: bool) -> Option<String> {
    let (mut units, remainder) = bytes.as_chunks::<2>();
    if complete && !remainder.is_empty() {
        return None;
    }
    let unit = |pair: &[u8; 2]| {
        if little_endian {
            u16::from_le_bytes(*pair)
        } else {
            u16::from_be_bytes(*pair)
        }
    };
    // A sample may end halfway through a surrogate pair as well as a code unit.
    if !complete
        && units
            .last()
            .is_some_and(|pair| (0xd800..=0xdbff).contains(&unit(pair)))
    {
        units = &units[..units.len() - 1];
    }
    char::decode_utf16(units.iter().map(unit))
        .collect::<Result<String, _>>()
        .ok()
}

fn decode_utf32(bytes: &[u8], little_endian: bool, complete: bool) -> Option<String> {
    let (units, remainder) = bytes.as_chunks::<4>();
    if complete && !remainder.is_empty() {
        return None;
    }
    units
        .iter()
        .map(|unit| {
            char::from_u32(if little_endian {
                u32::from_le_bytes(*unit)
            } else {
                u32::from_be_bytes(*unit)
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::decode_index_text;
    use crate::domain::FileFormat;

    #[test]
    fn python_latin_one_cookie_preserves_legacy_characters() {
        let bytes = b"#!/usr/bin/env python\n# coding: latin_1\nTOTO = 'Caf\xe9'\n";
        let text = decode_index_text(&[FileFormat::Python], bytes).expect("Latin-1 Python");
        assert!(text.contains("Café"));
        assert!(!text.contains('\u{fffd}'));
    }

    #[test]
    fn invalid_utf_eight_bytes_are_replaced_without_guessing_latin_one() {
        let bytes = b"/* \xe9viter \xe9crasement */\n.test { margin: 1rem; }\n";
        let text = decode_index_text(&[FileFormat::Css], bytes).expect("legacy CSS");
        assert_eq!(text.matches('\u{fffd}').count(), 2);
        assert!(!text.contains('é'));
        assert!(text.contains(".test { margin: 1rem; }"));
        let dense = b"body { note: \xe9\xe9\xe9\xe9\xe9\xe9\xe9\xe9; }";
        let text = decode_index_text(&[FileFormat::Css], dense).expect("replace invalid bytes");
        assert_eq!(text.matches('\u{fffd}').count(), 8);
    }

    #[test]
    fn malformed_bom_and_binary_content_remain_errors() {
        assert!(decode_index_text(&[FileFormat::Text], &[0xff, 0xfe, 0xff]).is_none());
        let mut transport = vec![0xff; 188 * 3];
        for packet in transport.as_chunks_mut::<188>().0 {
            packet[..4].copy_from_slice(&[0x47, 0x1f, 0xff, 0x10]);
        }
        assert!(decode_index_text(&[FileFormat::TypeScript], &transport).is_none());
    }
}
