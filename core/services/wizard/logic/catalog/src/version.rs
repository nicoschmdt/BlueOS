#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct Version {
    pub major: u32,
    pub minor: u32,
    pub patch: u32,
}

pub fn coerce_version(text: &str) -> Option<Version> {
    let position = text.find(|c: char| c.is_ascii_digit())?;
    let rest = &text[position..];

    let end = rest
        .find(|c: char| c != '.' && !c.is_ascii_digit())
        .unwrap_or(rest.len());
    let version = &rest[..end];

    let mut parts = version
        .split('.')
        .map_while(|segment| segment.parse::<u32>().ok())
        .fuse();

    Some(Version {
        major: parts.next()?,
        minor: parts.next().unwrap_or(0),
        patch: parts.next().unwrap_or(0),
    })
}
