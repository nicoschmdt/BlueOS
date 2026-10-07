use crate::version::{Version, coerce_version};
use std::collections::BTreeMap;

pub type ParamValues = BTreeMap<String, f64>;
pub type ParamSets = BTreeMap<String, ParamValues>;

pub struct ParamSetKey {
    vehicle: String,
    version: Version,
    pub board: String,
}

// Keys look like "params/ardupilot/ArduSub/4.5/navigator/Standard BlueROV2.params".
pub fn parse_param_set_key(key: &str) -> Option<ParamSetKey> {
    let parts: Vec<&str> = key.split('/').collect();

    let &[_, _, vehicle, version, board, _] = parts.as_slice() else {
        return None;
    };

    Some(ParamSetKey {
        vehicle: vehicle.to_lowercase(),
        version: coerce_version(version)?,
        board: board.to_lowercase(),
    })
}

pub fn parse_param_sets(payload: &str) -> Result<ParamSets, serde_json::Error> {
    serde_json::from_str::<BTreeMap<String, ParamValues>>(payload)
}

// Directories are named after the platform, not the name, which is whatever the USB descriptor reports
// ("PX4 FMU v2.x" for a Pixhawk1). SITL is the only platform carrying a host arch suffix the repository lacks.
pub fn board_directory(board: &str) -> String {
    let platform = board.to_lowercase();
    if platform.starts_with("sitl") {
        return "sitl".to_string();
    }
    platform
}

pub fn param_sets_for_firmware(
    all_param_sets: &ParamSets,
    vehicle_type: &str,
    version: &Version,
    board: &str,
) -> ParamSets {
    let wanted_vehicle = vehicle_type.to_lowercase();
    let wanted_board = board_directory(board);

    let candidates: Vec<(&String, &ParamValues, Version)> = all_param_sets
        .iter()
        .filter_map(|(key, value)| {
            let parsed = parse_param_set_key(key)?;
            let usable = parsed.vehicle == wanted_vehicle
                && parsed.board == wanted_board
                // Sets carry forward until a newer one is curated, but never across a major that may rename parameters
                && parsed.version.major == version.major
                && parsed.version <= *version;
            usable.then_some((key, value, parsed.version))
        })
        .collect();

    let Some(newest) = candidates
        .iter()
        .map(|(_, _, set_version)| set_version)
        .max()
    else {
        return ParamSets::new();
    };

    candidates
        .iter()
        .filter(|(_, _, set_version)| set_version == newest)
        .map(|(key, value, _)| (key.to_string(), (*value).clone()))
        .collect()
}
